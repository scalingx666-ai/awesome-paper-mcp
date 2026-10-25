"""Child-process MCP bridge driven by raw JSON-RPC over stdio.

Why not mcp.client.stdio?
--------------------------
The installed ``mcp`` 1.30.0 client deadlocks in ``stdio_client`` +
``ClientSession.initialize()`` on this platform (anyio 4.15 task-group /
cancel-scope interaction), so the bridge speaks the protocol directly. The
upstream servers are spec-compliant over stdio, which was verified with raw
JSON-RPC against all backends before this module was written.

Protocol notes
--------------
* Requests are newline-delimited JSON on the child's stdin.
* Responses and notifications are newline-delimited JSON on stdout.
* The child writes human-readable logs to stderr; those are never parsed.
* A strict ``initialize`` -> ``notifications/initialized`` -> request order is
  required by the spec and by the low-level ``Server`` implementations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .registry import Backend

log = logging.getLogger("unified-mcp.bridge")

PROTOCOL_VERSION = "2025-03-26"
_LEGACY_PROTOCOL_VERSION = "2024-11-05"

_PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def _sanitize_proxy(env: Dict[str, str]) -> Dict[str, str]:
    """Drop loopback proxy settings that are not actually listening.

    Some hosts export ``http_proxy=127.0.0.1:7890`` globally while the proxy
    app is closed, which makes every upstream HTTP call fail with
    ProxyError. A quick connect probe decides whether to keep or strip them.
    """
    target = env.get("http_proxy") or env.get("HTTP_PROXY") or env.get("all_proxy") or env.get("ALL_PROXY")
    if not target:
        return env
    host, port = _parse_loopback_proxy(target)
    if host is None:
        return env
    if _port_open(host, port):
        return env
    log.debug("stripping unreachable proxy %s:%s from child env", host, port)
    for key in _PROXY_KEYS:
        env.pop(key, None)
    env["no_proxy"] = "*"
    env["NO_PROXY"] = "*"
    return env


def _parse_loopback_proxy(value: str):
    from urllib.parse import urlparse

    try:
        parsed = urlparse(value if "://" in value else f"http://{value}")
    except Exception:
        return None, None
    host = parsed.hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        return None, None
    return host, parsed.port or 7890


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


class BackendError(RuntimeError):
    pass


class _Subprocess:
    """A running upstream MCP server speaking newline-delimited JSON-RPC."""

    def __init__(self, name: str, command: List[str], env: Dict[str, str]) -> None:
        self.name = name
        self.command = command
        self.env = env
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._server_info: Dict[str, Any] = {}

    async def start(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            return

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        for k, v in self.env.items():
            if v:
                env[k] = v
        # Upstream searchers honour http_proxy/https_proxy. If a local proxy is
        # configured but unreachable, direct connections still work, so strip a
        # dead proxy rather than failing every network call.
        env = _sanitize_proxy(env)

        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "unified-paper-mcp", "version": "1.0.0"},
            },
            timeout=60,
        )
        await self._notify("notifications/initialized")

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and isinstance(msg.get("id"), int):
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception as exc:  # pragma: no cover
            log.debug("reader loop for %s ended: %s", self.name, exc)
        finally:
            # Unblock anything still waiting.
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(BackendError(f"backend '{self.name}' closed"))
            self._pending.clear()

    async def _send(self, payload: Dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        data = (json.dumps(payload) + "\n").encode()
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def _request(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 60
    ) -> Any:
        if self.proc is None or self.proc.returncode is not None:
            raise BackendError(f"backend '{self.name}' is not running")
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._send(payload)
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise BackendError(
                f"backend '{self.name}' timed out on {method} after {timeout}s"
            ) from exc
        if "error" in msg:
            err = msg["error"]
            raise BackendError(
                f"backend '{self.name}' {method} error {err.get('code')}: {err.get('message')}"
            )
        return msg.get("result")

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except BaseException:
                pass
            self._reader_task = None
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:  # pragma: no cover
            pass
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:  # pragma: no cover
                    pass


class BackendProxy:
    """Lazily starts one upstream server and caches its tool list."""

    def __init__(self, backend: Backend, extra_env: Optional[Dict[str, str]] = None) -> None:
        self.backend = backend
        self.name = backend.name
        self._child: Optional[_Subprocess] = None
        self._tools: Optional[List[Dict[str, Any]]] = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> _Subprocess:
        async with self._lock:
            if self._child is None:
                child = _Subprocess(self.name, self.backend.command, self.backend.env)
                await child.start()
                self._child = child
            return self._child

    async def _restart(self) -> _Subprocess:
        async with self._lock:
            if self._child is not None:
                await self._child.stop()
                self._child = None
            self._tools = None
            return await self._ensure()

    # ------------------------------------------------------------------ tools
    async def list_tools(self, refresh: bool = False) -> List[Dict[str, Any]]:
        child = await self._ensure()
        if self._tools is None or refresh:
            result = await child._request("tools/list", timeout=60)
            tools = (result or {}).get("tools", [])
            self._tools = [
                {
                    "name": t.get("name"),
                    "description": t.get("description") or "",
                    "inputSchema": t.get("inputSchema")
                    or {"type": "object", "properties": {}},
                }
                for t in tools
                if t.get("name")
            ]
        return list(self._tools or [])

    async def call_tool(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        try:
            child = await self._ensure()
        except Exception as exc:
            raise BackendError(f"backend '{self.name}' failed to start: {exc}") from exc

        try:
            result = await child._request(
                "tools/call",
                {"name": tool, "arguments": arguments or {}},
                timeout=180,
            )
        except BackendError:
            # The child may have died; restart once and retry.
            child = await self._restart()
            result = await child._request(
                "tools/call",
                {"name": tool, "arguments": arguments or {}},
                timeout=180,
            )
        return self._normalize(result)

    @staticmethod
    def _normalize(result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        is_error = bool(result.get("isError"))
        content = result.get("content") or []
        parts: List[Any] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif "data" in item:
                parts.append(item["data"])
        decoded: List[Any] = []
        for p in parts:
            if isinstance(p, str):
                try:
                    decoded.append(json.loads(p))
                    continue
                except Exception:
                    pass
            decoded.append(p)
        if not decoded:
            return None
        if len(decoded) == 1:
            single = decoded[0]
            if is_error and isinstance(single, (dict, list)):
                return {"error": True, "detail": single}
            return single
        if is_error:
            return {"error": True, "detail": decoded}
        return decoded

    async def close(self) -> None:
        async with self._lock:
            if self._child is not None:
                await self._child.stop()
                self._child = None
