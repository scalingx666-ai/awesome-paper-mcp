"""Registry of upstream backends.

Each entry describes how to launch one upstream MCP server as a child process.
``tool_aliases`` maps a generic verb onto the backend's real tool name so the
gateway can expose cross-source tools without knowing every backend's naming.

Notes
-----
* ``openreview`` (OpenCodice) and ``openreview_classic`` (stepp1/anyakors
  equivalent) are two different OpenReview servers with different tool sets;
  both are kept because they cover different ground (review/rebuttal/decision
  vs. bulk conference listing + PDF export).
* ``google_scholar`` (JackKuo666) is not installable as a package, so it is
  launched by pointing python at its ``google_scholar_server.py`` file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Resolved at import time so the registry works regardless of cwd.
import os
import sys

_UNIFIED_DIR = os.path.dirname(os.path.abspath(__file__))     # unified_mcp/backends/
_ROOT = os.path.dirname(os.path.dirname(_UNIFIED_DIR))        # awesome-paper-mcp/
_SOURCES = os.path.join(_ROOT, "sources")
_VENV_PY = os.path.join(_ROOT, ".venv", "bin", "python")
_SERCANTAS_DIST = os.path.join(_SOURCES, "sercantas-Academic-Research-MCP-Suite", "dist")
_NODE = "node"
if not os.path.exists(_VENV_PY):                          # pragma: no cover
    _VENV_PY = sys.executable

# The TypeScript suite is a Node project; resolve `node` once.
import shutil as _shutil

_NODE = _shutil.which("node") or "node"


@dataclass(frozen=True)
class Backend:
    name: str
    """Namespace prefix, e.g. ``arxiv``."""

    description: str
    """Human-readable summary of what this backend covers."""

    command: List[str]
    """argv used to launch the upstream MCP server over stdio."""

    env: Dict[str, str] = field(default_factory=dict)
    """Extra env vars for the child."""

    requires: List[str] = field(default_factory=list)
    """Env var names that should be set for this backend to be useful."""

    tool_aliases: Dict[str, str] = field(default_factory=dict)
    """generic verb -> this backend's real tool name."""

    lazy: bool = True
    """Start the child on first use instead of at server startup."""


# --- Python search/retrieval backends ---------------------------------------
_PYTHON_BACKENDS: List[Backend] = [
    Backend(
        name="arxiv",
        description="arXiv search, download, full-text reading, outlines, LaTeX source, citation graph and topic alerts (blazickjp/arxiv-mcp-server).",
        command=[_VENV_PY, "-m", "arxiv_mcp_server"],
        tool_aliases={
            "search": "search_papers",
            "read": "read_paper",
            "download": "download_paper",
            "details": "get_abstract",
            "outline": "get_paper_outline",
            "semantic_search": "semantic_search",
            "citations": "citation_graph",
            "latex": "get_paper_latex",
        },
    ),
    Backend(
        name="multisearch",
        description="40+ academic sources in one tool: arXiv, PubMed, bioRxiv, medRxiv, Semantic Scholar, Crossref, OpenAlex, DBLP, DOAJ, HAL, CORE, Europe PMC, SSRN, Zenodo, CiteSeerX, WoS, Scopus, IEEE, OpenReview, IACR (openags/paper-search-mcp).",
        command=[_VENV_PY, "-m", "paper_search_mcp.server"],
        requires=["PAPER_SEARCH_MCP_UNPAYWALL_EMAIL"],
        tool_aliases={
            "search": "search_papers",
            "read": "read_paper",
            "download": "download_paper",
            "details": "get_paper_details",
            "citations": "get_citing_papers",
            "references": "get_referenced_papers",
            "crossref_search": "search_crossref",
            "doi": "get_crossref_paper_by_doi",
        },
    ),
    Backend(
        name="resp",
        description="SERP-free multi-source scholarly search across arXiv, Semantic Scholar, OpenReview, OpenAlex, DBLP, ACL Anthology, ACM, ConnectedPapers plus conference listings (NeurIPS/IJCAI/CVPR/ECCV/AAAI/PMLR) (monk1337/resp_mcp).",
        command=[_VENV_PY, "-m", "respmcp.server"],
        tool_aliases={
            "search": "search_semantic_scholar",
            "search_all": "search_all",
            "arxiv": "search_arxiv",
            "openalex": "search_openalex",
            "dblp": "search_dblp",
            "acl": "search_acl",
            "openreview": "search_openreview",
            "connected": "search_connected_papers",
            "conferences": "list_conferences",
            "citations": "get_citations",
            "references": "get_references",
        },
    ),
    Backend(
        name="openreview",
        description="OpenReview review data: submissions, reviews, meta-reviews, rebuttals and decisions across NeurIPS/ICLR/ACL ARR/COLM/TMLR (OpenCodice-Research/openreview-mcp).",
        command=[_VENV_PY, "-m", "openreview_mcp.cli"],
        env={"OPENREVIEW_USERNAME": "", "OPENREVIEW_PASSWORD": ""},
        requires=["OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"],
        tool_aliases={
            "search": "openreview_search_submissions",
            "details": "openreview_get_submission",
            "reviews": "openreview_get_reviews",
            "decision": "openreview_get_decision",
            "venues": "openreview_list_venues",
            "author": "openreview_search_by_author",
        },
    ),
    Backend(
        name="openreview_classic",
        description="OpenReview bulk conference listing and JSON/PDF export of full paper text for ICML/ICLR/NeurIPS (anyakors/openreview-mcp-server).",
        command=[_VENV_PY, "-m", "openreview_mcp_server"],
        env={"OPENREVIEW_USERNAME": "", "OPENREVIEW_PASSWORD": ""},
        requires=["OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"],
        tool_aliases={
            "search": "search_papers",
            "conference": "get_conference_papers",
            "export": "export_papers",
            "author": "get_user_papers",
        },
    ),
    Backend(
        name="scholar",
        description="Semantic Scholar API: paper search, details, citations, references, author info and recommendations (silung/scholar-search-mcp).",
        command=[_VENV_PY, "-m", "scholar_search_mcp"],
        tool_aliases={
            "search": "search_papers",
            "details": "get_paper_details",
            "citations": "get_paper_citations",
            "references": "get_paper_references",
            "author": "get_author_info",
            "author_papers": "get_author_papers",
            "recommend": "get_recommendations",
        },
    ),
    Backend(
        name="google_scholar",
        description="Google Scholar keyword/advanced search and author profiles (JackKuo666/Google-Scholar-MCP-Server).",
        # This repo is not an installable package and imports a sibling module
        # by bare name, so it must be launched as a script (not -m) from its
        # own directory to keep its flat import working.
        command=[
            _VENV_PY,
            os.path.join(_SOURCES, "JackKuo666-Google-Scholar-MCP-Server", "google_scholar_server.py"),
        ],
        tool_aliases={
            "search": "search_google_scholar_key_words",
            "advanced": "search_google_scholar_advanced",
            "author": "get_author_info",
        },
    ),
    Backend(
        name="papers_helper",
        description="Local paper library with FTS5/BM25 search over arXiv + CVF/AAAI/ACL/IJCAI/ICML crawlers, table-of-contents extraction and in-PDF grep (elpaca/ai_papers_helper).",
        command=[_VENV_PY, "-m", "ai_papers_helper.mcp_server"],
        requires=["MINERU_API_KEY"],
        tool_aliases={
            "search": "search_library_papers",
            "arxiv": "search_arxiv_papers",
            "toc": "get_paper_toc",
            "grep": "grep_paper_content",
        },
    ),
]

# --- sercantas/Academic-Research-MCP-Suite (TypeScript, Node) ---------------
# A research *pipeline* rather than a search index: question refinement, data
# wrangling, code generation/execution and report writing. Each stage is its
# own MCP server, so they are mounted as six separate backends.

_SERCANTAS: List[Backend] = [
    Backend(
        name="research_initiator",
        description="Refine a vague research question into a testable, scoped hypothesis (sercantas/Academic-Research-MCP-Suite).",
        command=[_NODE, os.path.join(_SERCANTAS_DIST, "research_initiator_developer_server.js")],
        tool_aliases={"refine": "refine"},
    ),
    Backend(
        name="data_wrangler",
        description="Clean, validate and prepare research datasets for analysis (sercantas suite).",
        command=[_NODE, os.path.join(_SERCANTAS_DIST, "data_wrangler.js")],
        tool_aliases={"process": "process"},
    ),
    Backend(
        name="code_generator",
        description="Generate statistical analysis scripts in Python, R or JavaScript (sercantas suite).",
        command=[_NODE, os.path.join(_SERCANTAS_DIST, "code_generator_v2.js")],
        tool_aliases={"generate": "generate"},
    ),
    Backend(
        name="code_executor",
        description="Execute generated analysis scripts and capture stdout/stderr (sercantas suite).",
        command=[_NODE, os.path.join(_SERCANTAS_DIST, "code_executor.js")],
        tool_aliases={"run": "run"},
    ),
    Backend(
        name="research_writer",
        description="Compose structured academic reports in APA / ASA / generic style (sercantas suite).",
        command=[_NODE, os.path.join(_SERCANTAS_DIST, "research_writer.js")],
        tool_aliases={"compose": "compose"},
    ),
    Backend(
        name="research_orchestrator",
        description="Coordinate the full research pipeline end to end: initiate, process, generate, execute, write (sercantas suite).",
        command=[_NODE, os.path.join(_SERCANTAS_DIST, "research_orchestrator.js")],
        tool_aliases={"initiate": "initiate"},
    ),
]

# The TypeScript research-pipeline suite.
SERCANTAS: List[Backend] = _SERCANTAS
BY_NAME: Dict[str, Backend] = {b.name: b for b in _PYTHON_BACKENDS + _SERCANTAS}

# All backends, search first, pipeline stages after.
ALL_BACKENDS: List[Backend] = _PYTHON_BACKENDS + _SERCANTAS

# Backwards-compatible alias: the search/retrieval backends.
BACKENDS: List[Backend] = _PYTHON_BACKENDS


def get(name: str) -> Optional[Backend]:
    return BY_NAME.get(name)
