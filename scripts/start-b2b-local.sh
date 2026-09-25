#!/usr/bin/env bash
# Start the Salesforce B2B merchant backend locally.
#
# Credentials are read from examples/salesforce/.env — already configured
# for atci-b2b-lex--b2bpoc.sandbox.my.salesforce.com.
#
# Usage:
#   ./scripts/start-b2b-local.sh           # merchant API on :8000
#   ./scripts/start-b2b-local.sh mcp       # merchant MCP server on :8201
#   ./scripts/start-b2b-local.sh all       # both in parallel
#
# Requires: a virtualenv with requirements.txt installed.
#   python3.12 -m venv .venv && source .venv/bin/activate && ./scripts/install.sh

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-api}"

# Load B2B credentials into the current shell so both services pick them up.
if [ -f examples/salesforce/.env ]; then
    set -a
    # shellcheck disable=SC1091
    source examples/salesforce/.env
    set +a
    echo "Loaded examples/salesforce/.env"
    echo "  SF_INSTANCE_URL = ${SF_INSTANCE_URL:-}"
fi

export MERCHANT_MCP_UNSAFE_ALLOW_NO_AUTH=1
export MERCHANT_REQUIRE_HOST_APPROVAL=0

start_api() {
    echo ""
    echo "Starting merchant API on http://localhost:8000 ..."
    echo "  → merchant-web: set NEXT_PUBLIC_API_URL=http://localhost:8000"
    echo ""
    uvicorn salesforce.api.main:app --app-dir examples --reload --port 8000
}

start_mcp() {
    echo ""
    echo "Starting merchant MCP server on http://localhost:8201/mcp ..."
    echo "  → Claude Code: set MERCHANT_MCP_URL=http://localhost:8201/mcp"
    echo ""
    python scripts/merchant_mcp_server_sf.py
}

case "$MODE" in
    api)  start_api ;;
    mcp)  start_mcp ;;
    all)
        start_api &
        API_PID=$!
        start_mcp &
        MCP_PID=$!
        trap "kill $API_PID $MCP_PID 2>/dev/null" EXIT INT TERM
        wait
        ;;
    *)
        echo "Usage: $0 [api|mcp|all]" >&2
        exit 2
        ;;
esac
