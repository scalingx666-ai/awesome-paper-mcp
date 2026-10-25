"""Unified Paper MCP — one MCP server that aggregates 9 upstream paper-search MCP servers.

Architecture
------------
The gateway runs a single MCP server (stdio or streamable-http). Every upstream
server is launched as a *child process* on a private stdio pipe, so the nine
projects never share a Python interpreter and cannot break each other's
dependency pins. The gateway speaks MCP to each child over stdio, mirrors the
child's tool list into its own namespace, and forwards calls.

Namespacing
-----------
Tools are exposed as ``<backend>__<tool>`` (e.g. ``arxiv__search_papers``).
A small set of high-value cross-source tools is also exposed unprefixed so
agents can just call ``search_papers`` without picking a backend.

Because each upstream project uses its own tool names, a thin alias layer maps
common verbs (search / read / download / details / citations) onto each
backend's real tool name. See ``backends/registry.py``.
"""

from __future__ import annotations

__version__ = "1.0.0"
