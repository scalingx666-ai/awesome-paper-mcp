# Awesome Paper MCP — Unified Academic Paper Search

One MCP server that unifies **9 upstream paper-search MCP servers** into a single
endpoint. Point any MCP-capable agent at it and get search, full-text reading,
citation graphs, peer-review data and a research pipeline from 40+ academic
sources — arXiv, Semantic Scholar, Google Scholar, OpenReview, OpenAlex, DBLP,
PubMed, bioRxiv, medRxiv, Crossref, CORE, HAL, Europe PMC, SSRN, Zenodo,
CiteSeerX, DOAJ, BASE, OpenAIRE, PMC, ACM, IACR, WoS, Scopus, IEEE, plus
conference listings for NeurIPS / ICML / ICLR / IJCAI / CVPR / ECCV / AAAI / PMLR.

**14 backends · 148 upstream tools · 1 config entry.**

---

## Why a gateway instead of installing 9 servers

Each upstream project pins its own dependency versions (`arxiv` 2.x vs 4.x,
`bibtexparser` 1.x vs 2.x, `mcp` 0.9 vs 1.30, `fastmcp` 3.x vs 4.x). Installing
them into one interpreter breaks them. This gateway therefore runs each upstream
server as a **separate child process** on a private stdio pipe and speaks MCP to
it, so no two projects ever share an interpreter.

```
 agent ──stdio/http──▶ paper-mcp gateway ──┬─▶ arxiv (19 tools)
                                          ├─▶ multisearch (71 tools)
                                          ├─▶ resp (20 tools)
                                          ├─▶ openreview (11 tools)
                                          ├─▶ openreview_classic (5 tools)
                                          ├─▶ scholar (9 tools)
                                          ├─▶ google_scholar (3 tools)
                                          ├─▶ papers_helper (4 tools)
                                          └─▶ 6 × sercantas research pipeline (6 tools)
```

---

## Install

One command from a fresh clone (needs `uv`, `git`, `node` ≥ 18, `npm`):

```bash
git clone https://github.com/<you>/awesome-paper-mcp.git
cd awesome-paper-mcp
bash scripts/setup.sh
```

The script creates the Python 3.12 venv, installs the pinned dependency set
(`requirements-lock.txt`), clones the 9 upstream repos at the exact commits this
gateway was tested against, links them into the same venv with `--no-deps`
(that is what lets their incompatible pins coexist), installs the Node modules
for the sercantas pipeline, installs the gateway itself, and finishes with an
MCP handshake smoke test. It is safe to re-run.

<details>
<summary>Manual install (equivalent, step by step)</summary>

```bash
cd awesome-paper-mcp
uv venv --python 3.12 .venv
export VIRTUAL_ENV=$PWD/.venv PATH=$PWD/.venv/bin:$PATH

uv pip install -r requirements-lock.txt           # pinned shared dependency set
git clone <upstream-url> sources/<dir> && git -C sources/<dir> checkout <sha>   # ×9, see scripts/setup.sh
for d in blazickjp-arxiv-mcp-server openags-paper-search-mcp monk1337-resp_mcp \
         OpenCodice-Research-openreview-mcp anyakors-openreview-mcp-server-equivalent \
         silung-scholar-search-mcp elpaca-ai_papers_helper; do
  uv pip install -e "sources/$d" --no-deps        # pins cannot fight each other
done
( cd sources/sercantas-Academic-Research-MCP-Suite && npm install --no-audit --no-fund )
uv pip install -e . --no-deps                     # gateway -> .venv/bin/paper-mcp
```

`JackKuo666-Google-Scholar-MCP-Server` is deliberately not installed: it imports
a sibling module by bare name, so the gateway launches it as a script instead.
</details>

---

## Connect your agent

Replace `/path/to/awesome-paper-mcp` with your checkout everywhere below. If
your client supports a `cwd` field, the `python -m unified_mcp` form works; if
it does not (e.g. MiniMax Code), point `command` at the console script
`/path/to/awesome-paper-mcp/.venv/bin/paper-mcp` with no `args` — the gateway
resolves its own paths from the script location.

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "paper-mcp": {
      "command": "/path/to/awesome-paper-mcp/.venv/bin/python",
      "args": ["-m", "unified_mcp"],
      "cwd": "/path/to/awesome-paper-mcp"
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "paper-mcp": {
      "command": "/path/to/awesome-paper-mcp/.venv/bin/python",
      "args": ["-m", "unified_mcp"],
      "cwd": "/path/to/awesome-paper-mcp"
    }
  }
}
```

### MiniMax Code (`~/.minimax/mcp.json` or project `.mcp.json`)

MiniMax Code's MCP settings have no `cwd` field, so use the console script:

```json
{
  "mcpServers": {
    "paper-mcp": {
      "command": "/path/to/awesome-paper-mcp/.venv/bin/paper-mcp",
      "args": []
    }
  }
}
```

### HTTP transport (shared / remote)

```bash
.venv/bin/python -m unified_mcp --http --host 127.0.0.1 --port 8765
```

```json
{
  "mcpServers": {
    "paper-mcp": { "type": "http", "url": "http://127.0.0.1:8765/mcp" }
  }
}
```

### Generic stdio (Gemini CLI, Cline, Windsurf, Zed, OpenCode, …)

```json
{
  "mcpServers": {
    "paper-mcp": {
      "command": "python",
      "args": ["-m", "unified_mcp"],
      "cwd": "/path/to/awesome-paper-mcp"
    }
  }
}
```

---

## The 14 backends

| Backend | Source repo | Tools | What it is for |
|---|---|---|---|
| `arxiv` | [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) | 19 | arXiv search, PDF download, full-text reading, outlines, LaTeX source, citation graph, topic alerts |
| `multisearch` | [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp) | 71 | 40+ sources in one server: PubMed, bioRxiv, medRxiv, Semantic Scholar, Crossref, OpenAlex, DBLP, HAL, CORE, DOAJ, Zenodo, SSRN, WoS, Scopus, IEEE, … |
| `resp` | [monk1337/resp_mcp](https://github.com/monk1337/resp_mcp) | 20 | SERP-free multi-source search + conference listings (NeurIPS/IJCAI/CVPR/ECCV/AAAI/PMLR) |
| `openreview` | [OpenCodice-Research/openreview-mcp](https://github.com/OpenCodice-Research/openreview-mcp) | 11 | Reviews, meta-reviews, rebuttals, decisions for NeurIPS/ICLR/ACL ARR/COLM/TMLR |
| `openreview_classic` | anyakors/openreview-mcp-server | 5 | Bulk conference listing + JSON/PDF full-text export |
| `scholar` | [silung/scholar-search-mcp](https://github.com/silung/scholar-search-mcp) | 9 | Semantic Scholar details, citations, references, authors, recommendations |
| `google_scholar` | [JackKuo666/Google-Scholar-MCP-Server](https://github.com/JackKuo666/Google-Scholar-MCP-Server) | 3 | Google Scholar keyword/advanced search + author profiles |
| `papers_helper` | [elpaca/ai_papers_helper](https://github.com/elpaca/ai_papers_helper) | 4 | Local FTS5/BM25 paper library, TOC extraction, in-PDF grep |
| `research_initiator` | [sercantas/Academic-Research-MCP-Suite](https://github.com/sercantas/Academic-Research-MCP-Suite) | 1 | Refine a question into a testable hypothesis |
| `data_wrangler` | sercantas | 1 | Clean and prepare datasets |
| `code_generator` | sercantas | 1 | Generate Python/R/JS analysis scripts |
| `code_executor` | sercantas | 1 | Execute analysis scripts, capture output |
| `research_writer` | sercantas | 1 | Compose APA / ASA / generic reports |
| `research_orchestrator` | sercantas | 1 | Coordinate the whole pipeline |

> **Note on `anyakors/openreview-mcp-server`** — that URL returns 401 to `git
> clone` and 404 to the GitHub tarball API, so it could not be fetched. Its
> published content is byte-identical to
> [stepp1/openreview-mcp-server](https://github.com/stepp1/openreview-mcp-server)
> (same tools, same README), which is what `openreview_classic` runs. If the
> original becomes reachable, replace `sources/anyakors-openreview-mcp-server-equivalent`.

---

## How to use it

### 1. Discover what is available

```
list_sources()
```

Returns every backend, its status (`ready` / `needs_config` / `unavailable`),
the env vars it is missing, and its full tool list.

### 2. Search across many sources at once

```
search_papers(query="mixture of experts routing", max_results=10)
```

Fans out to `arxiv`, `multisearch`, `resp` and `google_scholar` in parallel and
merges the results. Pass `sources=["scholar","openreview"]` to narrow it.

### 3. Get one paper by id

```
get_paper(paper_id="2401.04088")     # arXiv id
get_paper(paper_id="10.1145/xxx")    # DOI
```

Auto-detects the id shape and tries the backends that can resolve it.

### 4. Read full text

```
get_paper_text(paper_id="2401.04088", source="arxiv", max_chars=20000)
```

`arxiv` handles arXiv ids, `multisearch` handles arXiv/PubMed/bioRxiv/medRxiv/
Crossref/OpenAlex, `openreview_classic` exports OpenReview PDFs.

### 5. Call any specific upstream tool

```
call_source_tool(backend="arxiv", tool="get_paper_latex", arguments={"paper_id":"2401.04088"})
```

Or, after `list_sources()` has mounted them, call the namespaced form directly:

```
arxiv__get_paper_latex(arguments={"paper_id": "2401.04088"})
multisearch__search_pubmed(arguments={"query": "CRISPR off-target", "max_results": 10})
openreview__openreview_get_reviews(arguments={"submission_id": "abc123"})
resp__search_neurips(arguments={"keyword": "diffusion", "year": 2024})
```

Namespaced tools take their arguments as a single `arguments` object and forward
it verbatim, so every upstream parameter is available. Their descriptions list
each parameter, its type and whether it is required.

### 6. Run the research pipeline

```
run_research_pipeline(question="does test-time compute scaling help reasoning?", output_style="APA")
```

---

## Credentials

Everything works with **no credentials at all**. These only unlock more:

| Env var | Backend | Effect |
|---|---|---|
| `SEMANTIC_SCHOLAR_API_KEY` | `scholar`, `multisearch`, `resp` | Removes Semantic Scholar 429 rate limits |
| `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` | `multisearch` | Enables Unpaywall open-access PDF fallback |
| `PAPER_SEARCH_MCP_CORE_API_KEY` | `multisearch` | CORE full-text search |
| `PAPER_SEARCH_MCP_IEEE_API_KEY` | `multisearch` | IEEE Xplore metadata |
| `OPENREVIEW_USERNAME` / `OPENREVIEW_PASSWORD` | `openreview`, `openreview_classic` | Non-anonymous OpenReview queries |
| `MINERU_API_KEY` | `papers_helper` | PDF → markdown parsing |

Set them in the `env` block of your MCP config, e.g.:

```json
{
  "mcpServers": {
    "paper-mcp": {
      "command": "/path/to/awesome-paper-mcp/.venv/bin/python",
      "args": ["-m", "unified_mcp"],
      "cwd": "/path/to/awesome-paper-mcp",
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": "...",
        "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "you@example.com"
      }
    }
  }
}
```

---

## Layout

```
awesome-paper-mcp/
├── unified_mcp/                 # the gateway (the only code in this repo)
│   ├── server.py                # FastMCP gateway + smart tools + namespace mount
│   └── backends/
│       ├── registry.py          # the 14 backend definitions
│       └── proxy.py             # raw JSON-RPC stdio bridge to each child
├── scripts/setup.sh             # one-command bootstrap (clones + installs everything)
├── requirements-lock.txt        # pinned shared dependency set (144 packages)
├── mcp-configs.json             # ready-to-paste configs for 10 MCP clients
├── pyproject.toml               # console script: paper-mcp
├── sources/                     # 9 upstream repos — fetched by setup.sh, not committed
├── .venv/                       # Python 3.12 environment — created by setup.sh
└── README.md
```

The upstream projects are **not vendored**: `sources/` is git-ignored and
populated by `scripts/setup.sh` at the commits recorded in that script, so they
keep their own history, licenses and update cadence.

## Design notes

**Why raw JSON-RPC instead of `mcp.client.stdio`?** The installed `mcp` 1.30.0
client deadlocks inside `stdio_client` + `ClientSession.initialize()` on this
platform (an anyio 4.15 task-group / cancel-scope interaction). All upstream
servers answer correctly to raw newline-delimited JSON-RPC over stdio, which was
verified against every backend first, so `backends/proxy.py` speaks the protocol
directly. If a future `mcp` release fixes the client, swapping in the official
client is a drop-in change confined to `proxy.py`.

**Why lazy startup?** A backend is only spawned when first used, so one slow or
broken source never delays the handshake or blocks the others. `--no-lazy`
warms everything at boot instead.

**Proxy handling.** If the host exports an `http_proxy` pointing at a loopback
port that is not listening, the child processes detect it and fall back to a
direct connection instead of failing every request with `ProxyError`.

## License

[MIT](LICENSE) for the gateway code in `unified_mcp/` and everything else in
this repository. The upstream projects under `sources/` are downloaded at
install time and keep their own licenses — nothing here redistributes them.
