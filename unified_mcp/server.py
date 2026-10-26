"""Unified Paper MCP gateway.

Runs one MCP server that mirrors every upstream backend's tools under a
``<backend>__<tool>`` namespace, plus a small set of cross-source convenience
tools that fan out to several backends at once.

Usage
-----
    paper-mcp                 # stdio (default)
    paper-mcp --http          # streamable HTTP on 127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

from .backends import ALL_BACKENDS, Backend, BackendProxy, get
from .dedupe import merge_payloads

log = logging.getLogger("unified-mcp")

mcp = FastMCP(
    "paper-mcp",
    instructions=(
        "Unified academic paper search across arXiv, Semantic Scholar, Google Scholar, "
        "OpenReview, OpenAlex, DBLP, PubMed, bioRxiv, medRxiv, Crossref, CORE, HAL and 30+ "
        "more sources. Prefer the unprefixed cross-source tools (search_papers, get_paper, "
        "get_paper_text) for simple requests; search_papers merges duplicate papers across "
        "backends into one entry with a unified `sources` list. Use <backend>__<tool> when "
        "you need a specific source's full-text, review or export features. Call "
        "list_sources() to see every backend and its capabilities."
    ),
)

# ---------------------------------------------------------------------------
# Proxy management
# ---------------------------------------------------------------------------

_proxies: Dict[str, BackendProxy] = {}


def _proxy(name: str) -> BackendProxy:
    if name not in _proxies:
        backend = get(name)
        if backend is None:
            raise KeyError(f"unknown backend '{name}'")
        _proxies[name] = BackendProxy(backend)
    return _proxies[name]


async def _startup_proxies(lazy: bool) -> None:
    """Optionally warm up all backends so the first call is not slow."""
    if lazy:
        return
    for backend in ALL_BACKENDS:
        try:
            await _proxy(backend.name).list_tools()
        except Exception as exc:
            log.warning("backend %s unavailable at startup: %s", backend.name, exc)
    try:
        await mount_namespaced_tools()
    except Exception as exc:  # pragma: no cover
        log.warning("namespace mount failed at startup: %s", exc)


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------

_TOOL_CACHE: Dict[str, List[Dict[str, Any]]] = {}


async def _tools_for(backend_name: str) -> List[Dict[str, Any]]:
    if backend_name not in _TOOL_CACHE:
        _TOOL_CACHE[backend_name] = await _proxy(backend_name).list_tools()
    return _TOOL_CACHE[backend_name]


def _require_env(backend: Backend) -> List[str]:
    """Return the list of unset required env vars for this backend."""
    return [k for k in backend.requires if not os.environ.get(k)]


# ---------------------------------------------------------------------------
# Meta tools (always available, no upstream call needed)
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_sources() -> Dict[str, Any]:
    """List every paper source available through this server, with its
    capabilities, tool names and any missing credentials.

    Call this first to decide which backend to use.
    """
    sources: List[Dict[str, Any]] = []
    for backend in ALL_BACKENDS:
        missing = _require_env(backend)
        try:
            tools = await _tools_for(backend.name)
            tool_names = [t["name"] for t in tools]
            status = "ready" if not missing else "needs_config"
        except Exception as exc:
            tool_names = []
            status = f"unavailable: {type(exc).__name__}"
        sources.append(
            {
                "backend": backend.name,
                "description": backend.description,
                "status": status,
                "missing_env": missing,
                "tools": tool_names,
            }
        )
    # Discovering the sources also mounts their tools as <backend>__<tool>.
    await mount_namespaced_tools()
    return {"count": len(sources), "sources": sources}


@mcp.tool()
async def search_papers(
    query: str,
    sources: Optional[List[str]] = None,
    max_results: int = 10,
    year_from: Optional[int] = None,
) -> Dict[str, Any]:
    """Search for papers across one or many sources at once.

    Default sources are the ones that need no credentials: arxiv, multisearch
    (Semantic Scholar / Crossref / OpenAlex / DBLP / PubMed ...), resp and
    google_scholar.

    Results from every source are merged and de-duplicated before being
    returned in ``papers``: the same arXiv id, DOI, or title/first-author pair
    collapses into one entry whose ``sources`` list names every backend that
    returned it. Raw per-source payloads stay in ``results`` for inspection.

    Args:
        query: Keywords or a paper title.
        sources: Backend names from list_sources(). Omit for the default set.
        max_results: Results per source.
        year_from: Only papers from this year onwards (where supported).
    """
    chosen = sources or ["arxiv", "multisearch", "resp", "google_scholar"]
    per_source = max(1, min(max_results, 50))
    jobs: List[Tuple[str, str, Dict[str, Any]]] = []
    for name in chosen:
        backend = get(name)
        if backend is None:
            continue
        alias = backend.tool_aliases.get("search")
        if not alias:
            continue
        args: Dict[str, Any] = {"query": query, "max_results": per_source}
        if name == "multisearch":
            args = {"query": query, "max_results": per_source}
        elif name == "arxiv":
            args = {"query": query, "max_results": per_source}
        elif name == "resp":
            args = {"keyword": query, "max_results": per_source}
        elif name == "google_scholar":
            args = {"query": query, "num_results": per_source}
        elif name == "scholar":
            args = {"query": query, "max_results": per_source}
        jobs.append((name, alias, args))

    async def run(name: str, tool: str, args: Dict[str, Any]) -> Tuple[str, Any]:
        try:
            return name, await _proxy(name).call_tool(tool, args)
        except Exception as exc:
            return name, {"error": str(exc)}

    results = await asyncio.gather(*(run(*j) for j in jobs))
    merged = merge_payloads(results)
    out: Dict[str, Any] = {
        "query": query,
        "results": {name: data for name, data in results},
        "papers": [paper.to_dict() for paper in merged.papers],
        "total": len(merged.papers),
        "raw_total": merged.raw_total,
        "duplicates_removed": merged.duplicates_removed,
    }
    if merged.reasons:
        out["dedupe_reasons"] = merged.reasons
    return out


@mcp.tool()
async def get_paper(
    paper_id: str,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch metadata / abstract for one paper by its id.

    Args:
        paper_id: arXiv id (2401.04088), DOI (10.1234/xyz), OpenReview forum id,
            or a Semantic Scholar paper id.
        source: Force a specific backend. Omit to auto-detect.
    """
    order = _detect_sources(paper_id)
    if source:
        order = [source] + [s for s in order if s != source]

    for name in order:
        backend = get(name)
        if backend is None:
            continue
        alias = backend.tool_aliases.get("details") or backend.tool_aliases.get("search")
        if not alias:
            continue
        args = _details_args(name, alias, paper_id)
        try:
            data = await _proxy(name).call_tool(alias, args)
        except Exception as exc:
            log.debug("get_paper via %s failed: %s", name, exc)
            continue
        if data and not (isinstance(data, dict) and data.get("error")):
            return {"source": name, "paper": data}
    return {"error": f"could not resolve '{paper_id}' in any backend"}


def _detect_sources(paper_id: str) -> List[str]:
    pid = paper_id.strip()
    if pid.startswith("10."):
        return ["multisearch", "resp", "scholar"]
    if pid[:1].isdigit() and "." in pid.split("/")[0]:
        return ["arxiv", "multisearch", "resp"]
    return ["multisearch", "resp", "scholar", "openreview"]


def _details_args(name: str, alias: str, paper_id: str) -> Dict[str, Any]:
    if name == "arxiv":
        return {"paper_id": paper_id} if alias in ("get_abstract", "read_paper") else {"query": paper_id}
    if name == "scholar":
        return {"paper_id": paper_id}
    if name == "multisearch":
        if alias == "get_paper_details":
            return {"paper_id": paper_id}
        if alias == "get_crossref_paper_by_doi":
            return {"doi": paper_id}
        return {"query": paper_id, "max_results": 5}
    return {"query": paper_id, "max_results": 5}


@mcp.tool()
async def get_paper_text(
    paper_id: str,
    source: str = "arxiv",
    max_chars: int = 20000,
) -> Dict[str, Any]:
    """Download a paper and return its readable text.

    Args:
        paper_id: arXiv id, DOI, or backend-specific id.
        source: Backend to use. ``arxiv`` (default) supports arXiv ids;
            ``multisearch`` supports arXiv/PubMed/bioRxiv/medRxiv/Crossref/OpenAlex/...;
            ``openreview_classic`` exports OpenReview PDFs.
        max_chars: Truncate the returned text to this many characters.
    """
    candidates = {
        "arxiv": ("read_paper", {"paper_id": paper_id}),
        "multisearch": (None, {"paper_id": paper_id}),
        "openreview_classic": ("export_papers", {"query": paper_id, "max_papers": 1, "download_pdfs": True}),
    }
    if source not in candidates:
        return {"error": f"source '{source}' does not support full text; try arxiv, multisearch or openreview_classic"}

    backend = get(source)
    if backend is None:
        return {"error": f"unknown source '{source}'"}

    tool, args = candidates[source]
    if tool is None:
        # multisearch: pick read_<family>_paper based on id shape.
        tool = _multisearch_read_tool(paper_id)
        args = {"paper_id": paper_id}

    # The arxiv backend only serves text for papers already in its local
    # storage, so fetch it first. A failure here is not fatal: read_paper
    # reports the same condition with a clearer message.
    if source == "arxiv":
        try:
            await _proxy("arxiv").call_tool("download_paper", {"paper_id": paper_id})
        except Exception as exc:
            log.debug("arxiv download before read failed for %s: %s", paper_id, exc)

    try:
        data = await _proxy(source).call_tool(tool, args)
    except Exception as exc:
        return {"error": f"{source}::{tool} failed: {exc}"}

    text = _as_text(data)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated, {len(text)} chars total]"
    out: Dict[str, Any] = {"source": source, "tool": tool, "paper_id": paper_id, "text": text}
    # Surface pagination info when the backend chunks long papers.
    if isinstance(data, dict):
        for key in ("is_truncated", "next_start", "content_length", "returned_chars"):
            if key in data:
                out[key] = data[key]
        if data.get("next_retrieval"):
            out["next_retrieval"] = data["next_retrieval"]
    return out


def _multisearch_read_tool(paper_id: str) -> str:
    pid = paper_id.lower()
    if pid.startswith("10."):
        return "read_crossref_paper"
    if pid.startswith("pmid:"):
        return "read_pubmed_paper"
    if any(pid.startswith(p) for p in ("biorxiv:", "medrxiv:")):
        return "read_medrxiv_paper" if pid.startswith("medrxiv:") else "read_biorxiv_paper"
    return "read_arxiv_paper"


def _as_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, list) and data:
        return "\n".join(_as_text(d) for d in data)
    if isinstance(data, dict):
        for key in ("text", "content", "full_text", "markdown"):
            if isinstance(data.get(key), str):
                return data[key]
        import json

        return json.dumps(data, ensure_ascii=False, indent=2)
    return str(data)


# ---------------------------------------------------------------------------
# Dynamic passthrough: <backend>__<tool>
# ---------------------------------------------------------------------------


@mcp.tool()
async def call_source_tool(
    backend: str,
    tool: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call any tool from any backend by name.

    This is the escape hatch for backend-specific features that have no
    cross-source wrapper (e.g. ``openreview`` reviews, ``arxiv`` LaTeX source,
    ``resp`` conference listings). Use list_sources() to discover tool names.

    Args:
        backend: Backend name from list_sources().
        tool: Tool name as listed by list_sources().
        arguments: Tool arguments as a JSON object.
    """
    if get(backend) is None:
        return {"error": f"unknown backend '{backend}'; call list_sources() for valid names"}
    try:
        data = await _proxy(backend).call_tool(tool, arguments or {})
    except Exception as exc:
        return {"error": f"{backend}::{tool} failed: {exc}"}
    return {"backend": backend, "tool": tool, "result": data}


# ---------------------------------------------------------------------------
# Namespaced passthrough tools
# ---------------------------------------------------------------------------
# Every upstream tool is also mounted directly as ``<backend>__<tool>`` so an
# agent can call e.g. ``arxiv__get_paper_latex`` without going through
# call_source_tool. The namespaced tools are registered lazily by
# ``mount_namespaced_tools()`` (called on first use / at --no-lazy startup) so
# that a slow or broken backend never blocks server startup.


def _namespaced_tool_name(backend: str, tool: str) -> str:
    return f"{backend}__{tool}"


_mounted: set[str] = set()


def _register_namespaced(backend_name: str, tool: Dict[str, Any]) -> None:
    """Register one upstream tool as a FastMCP tool under its namespace.

    Upstream schemas rarely carry ``default`` values, and FastMCP refuses a
    ``None`` default on a non-nullable annotation. Rather than guessing
    defaults, each namespaced tool takes the upstream arguments as a single
    ``arguments`` object, mirroring ``call_source_tool`` — which validates
    cleanly and forwards the payload verbatim.
    """
    tool_name = tool["name"]
    full_name = _namespaced_tool_name(backend_name, tool_name)
    if full_name in _mounted:
        return

    description = tool.get("description") or ""
    schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    props = schema.get("properties") or {}
    required = schema.get("required") or []

    arg_doc = _describe_arguments(props, required)
    doc = f"[{backend_name}] {description}".strip()
    if arg_doc:
        doc = f"{doc}\n\nArguments:{arg_doc}"

    async def _impl(arguments: Dict[str, Any]) -> Any:
        try:
            return await _proxy(backend_name).call_tool(tool_name, arguments or {})
        except Exception as exc:
            return {"error": f"{backend_name}__{tool_name} failed: {exc}"}

    _impl.__name__ = full_name
    _impl.__qualname__ = full_name
    _impl.__doc__ = doc
    _impl.__annotations__ = {"arguments": Dict[str, Any], "return": Any}
    import inspect

    _impl.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                "arguments",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Dict[str, Any],
            )
        ]
    )

    mcp.add_tool(_impl, name=full_name, description=doc)
    _mounted.add(full_name)


def _describe_arguments(props: Dict[str, Any], required: List[str]) -> str:
    """Render a JSON-schema property map as a compact docstring block."""
    if not props:
        return ""
    lines = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        t = spec.get("type")
        if isinstance(t, list):
            t = "|".join(str(x) for x in t)
        flag = "required" if name in required else "optional"
        default = spec.get("default")
        default_note = f", default={default!r}" if default is not None else ""
        desc = (spec.get("description") or "").split("\n")[0][:160]
        lines.append(f"\n- {name} ({t}, {flag}{default_note}): {desc}")
    return "".join(lines)


async def mount_namespaced_tools(backends: Optional[List[str]] = None) -> int:
    """Mount every upstream tool as ``<backend>__<tool>``.

    Returns the number of newly mounted tools. Backends that fail to start are
    skipped rather than aborting the whole mount.
    """
    targets = backends or [b.name for b in ALL_BACKENDS]
    mounted = 0
    for name in targets:
        try:
            tools = await _tools_for(name)
        except Exception as exc:
            log.warning("skipping namespace mount for %s: %s", name, exc)
            continue
        for tool in tools:
            before = len(_mounted)
            _register_namespaced(name, tool)
            if len(_mounted) > before:
                mounted += 1
    return mounted


# ---------------------------------------------------------------------------
# Research pipeline (sercantas suite)
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_research_pipeline(
    question: str,
    output_style: str = "APA",
) -> Dict[str, Any]:
    """Run the full academic research pipeline on a research question.

    Stages: refine the question into a hypothesis, generate analysis code,
    execute it, then compose a structured report. Uses the sercantas
    Academic-Research-MCP-Suite backends.

    Args:
        question: A vague or broad research question.
        output_style: APA, ASA or generic.
    """
    stages: List[Tuple[str, str, Dict[str, Any]]] = [
        ("research_initiator", "refine", {"question": question}),
        ("code_generator", "generate", {"question": question}),
        ("research_writer", "compose", {"question": question, "style": output_style}),
    ]
    out: Dict[str, Any] = {"question": question, "stages": {}}
    for backend_name, tool, args in stages:
        try:
            data = await _proxy(backend_name).call_tool(tool, args)
            out["stages"][backend_name] = data
        except Exception as exc:
            out["stages"][backend_name] = {"error": str(exc)}
    return out


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper-mcp", description=__doc__)
    p.add_argument("--http", action="store_true", help="run as streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-lazy", action="store_true", help="start all backends at boot")
    p.add_argument("--debug", action="store_true")
    return p


def _quiet_stdio_loggers() -> None:
    """Keep stdio clean: route every library logger to stderr.

    On stdio transport, stdout *is* the JSON-RPC channel, so a single line of
    INFO output from FastMCP's own logger corrupts the stream for the client.
    ``logging.basicConfig`` only configures the root logger, and FastMCP adds
    its handler at import time, so re-target the whole hierarchy explicitly.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    for name in ("mcp", "mcp.server", "mcp.server.fastmcp", "unified-mcp", ""):
        logger = logging.getLogger(name)
        logger.handlers = [handler] if name else [handler]
        logger.propagate = False


def main(argv: Optional[List[str]] = None) -> None:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _quiet_stdio_loggers()
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
