#!/usr/bin/env bash
#
# Bootstrap a complete paper-mcp installation from a fresh clone.
#
#   bash scripts/setup.sh
#
# Steps: create the Python 3.12 venv -> install the pinned dependency set ->
# clone the 9 upstream repos at the commits this gateway was tested against ->
# install each upstream into the same venv with --no-deps -> install the Node
# dependencies of the sercantas pipeline -> install the gateway itself ->
# smoke-test the MCP handshake.
#
# Re-running is safe: already-cloned sources and the existing venv are reused.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  x %s\033[0m\n' "$*" >&2; exit 1; }

# --- prerequisites ----------------------------------------------------------
for cmd in uv git node npm; do
  command -v "$cmd" >/dev/null 2>&1 \
    || die "'$cmd' not found on PATH. Install it, then re-run this script."
done
node_major="$(node -p 'process.versions.node.split(".")[0]')"
[ "$node_major" -ge 18 ] || die "Node 18+ required (found $(node -v))"
command -v uv >/dev/null && [ -x "$(command -v uv)" ] || die "uv not executable"

# --- proxy sanity -----------------------------------------------------------
# A loopback proxy that is not actually listening makes every git/clone/install
# call fail with ProxyError. Probe it once and fall back to a direct connection.
proxy_url="${https_proxy:-${http_proxy:-}}"
proxy_port="$(printf '%s' "$proxy_url" | sed -n 's|.*127\.0\.0\.1:\([0-9][0-9]*\).*|\1|p')"
if [ -n "$proxy_port" ]; then
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$proxy_port") 2>/dev/null; then
    warn "proxy 127.0.0.1:$proxy_port is not listening — using a direct connection"
    export no_proxy='*' NO_PROXY='*'
    unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
  else
    exec 3>&- 2>/dev/null || true
  fi
fi

# --- python environment -----------------------------------------------------
log "Python 3.12 environment"
uv python find 3.12 >/dev/null 2>&1 || { warn "installing Python 3.12 via uv"; uv python install 3.12; }
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.12 .venv
else
  printf '  reusing existing .venv\n'
fi
export VIRTUAL_ENV="$REPO_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

log "Installing the pinned dependency set (requirements-lock.txt)"
uv pip install -q -r requirements-lock.txt

# --- upstream sources -------------------------------------------------------
# dir|url|commit — the commits this gateway was verified against. Bump them
# deliberately and re-run the regression checks when you want to track upstream.
SOURCES=(
  "blazickjp-arxiv-mcp-server|https://github.com/blazickjp/arxiv-mcp-server.git|572e301"
  "openags-paper-search-mcp|https://github.com/openags/paper-search-mcp.git|cab1b5c"
  "monk1337-resp_mcp|https://github.com/monk1337/resp_mcp.git|6e5727c"
  "OpenCodice-Research-openreview-mcp|https://github.com/OpenCodice-Research/openreview-mcp.git|4700f3d"
  "anyakors-openreview-mcp-server-equivalent|https://github.com/stepp1/openreview-mcp-server.git|1c86d22"
  "silung-scholar-search-mcp|https://github.com/silung/scholar-search-mcp.git|1392e7e"
  "JackKuo666-Google-Scholar-MCP-Server|https://github.com/JackKuo666/Google-Scholar-MCP-Server.git|738d60a"
  "elpaca-ai_papers_helper|https://github.com/elpaca/ai_papers_helper.git|f442148"
  "sercantas-Academic-Research-MCP-Suite|https://github.com/sercantas/Academic-Research-MCP-Suite.git|22c447b"
)

log "Upstream sources (sources/)"
mkdir -p sources
for entry in "${SOURCES[@]}"; do
  IFS='|' read -r dir url sha <<<"$entry"
  if [ -d "sources/$dir/.git" ]; then
    printf '  = sources/%s (already present)\n' "$dir"
    continue
  fi
  printf '  + sources/%s\n' "$dir"
  git clone --quiet "$url" "sources/$dir"
  git -C "sources/$dir" checkout --quiet "$sha"
done

# --- install upstreams side by side ----------------------------------------
# --no-deps is what makes 9 mutually incompatible pin sets coexist: every
# runtime dependency already comes from requirements-lock.txt, so the packages
# are linked in editable mode without resolving against each other.
# JackKuo666's server is not a package (it imports a sibling module by bare
# name), so it is launched as a script and needs no install here.
PY_PACKAGES=(
  "blazickjp-arxiv-mcp-server"
  "openags-paper-search-mcp"
  "monk1337-resp_mcp"
  "OpenCodice-Research-openreview-mcp"
  "anyakors-openreview-mcp-server-equivalent"
  "silung-scholar-search-mcp"
  "elpaca-ai_papers_helper"
)

log "Linking upstream Python packages into the venv (--no-deps)"
for dir in "${PY_PACKAGES[@]}"; do
  printf '  + %s\n' "$dir"
  uv pip install -q -e "sources/$dir" --no-deps
done

log "Node dependencies for the sercantas research pipeline"
( cd sources/sercantas-Academic-Research-MCP-Suite && npm install --no-audit --no-fund --loglevel=error )

# --- the gateway ------------------------------------------------------------
log "Installing the gateway (paper-mcp console script)"
uv pip install -q -e . --no-deps

# --- smoke test -------------------------------------------------------------
log "Smoke test: MCP handshake + tool inventory"
smoke="$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_sources","arguments":{}}}' \
  '{"jsonrpc":"2.0","id":4,"method":"tools/list"}' \
  | "$VIRTUAL_ENV/bin/paper-mcp")"

smoke_summary="$(printf '%s' "$smoke" | "$VIRTUAL_ENV/bin/python" -c '
import json, sys

meta_tools, mounted_tools, sources = [], [], []
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    result = msg.get("result") or {}
    tools = result.get("tools")
    if tools:
        names = [t["name"] for t in tools]
        if len(names) >= len(mounted_tools):
            mounted_tools = names
        if msg.get("id") == 2:
            meta_tools = names
    if msg.get("id") == 3:
        text = "".join(c.get("text", "") for c in result.get("content") or [])
        try:
            sources = (json.loads(text) or {}).get("sources") or []
        except ValueError:
            sources = []

print(len(meta_tools), len(mounted_tools))
for s in sources:
    print("%s\t%s\t%d" % (s.get("backend"), s.get("status"), len(s.get("tools") or [])))
')"

meta_count="$(printf '%s\n' "$smoke_summary" | sed -n 1p)"
total_count="$(printf '%s\n' "$smoke_summary" | sed -n 2p)"

if [ "${meta_count:-0}" -eq 0 ]; then
  die "handshake failed: the gateway returned no tools"
fi

printf '\033[1;32m  v handshake OK — %s smart tools always mounted\033[0m\n' "$meta_count"

if [ "${total_count:-0}" -gt "$meta_count" ]; then
  printf '\033[1;32m  v namespace mount OK — %s upstream tools exposed\033[0m\n' \
    "$((total_count - meta_count))"
else
  warn "no upstream tools mounted — every backend failed to start"
fi

printf '\n  %-24s %-14s %s\n' BACKEND STATUS TOOLS
printf '%s\n' "$smoke_summary" | sed 3d | awk -F'\t' '{ printf "  %-24s %-14s %s\n", $1, $2, $3 }'

if printf '%s\n' "$smoke_summary" | sed 3d | grep -q 'unavailable'; then
  warn "some backends are unavailable — see the table above (network or missing system tool)"
fi

cat <<EOF

\033[1;32mReady.\033[0m Point any MCP client at:

  $VIRTUAL_ENV/bin/paper-mcp          # console script, needs no cwd

or, if your client lets you set a working directory:

  $VIRTUAL_ENV/bin/python -m unified_mcp   (cwd: $REPO_ROOT)

Then call list_sources() to see the 14 backends and their status.
Credentials are optional — see the README table to unlock rate limits
and full-text sources.

EOF
