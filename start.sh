#!/usr/bin/env bash
# Dispatch entry point: one Dockerfile, three runtime modes.
# Set DEPLOY_MODE in the Railway service's environment variables:
#   (unset or "api")  →  UCP/Claude storefront API  (default, port $PORT)
#   "mcp"             →  Storefront MCP server       (port $PORT)
#   "gemini"          →  Gemini × UCP demo UI        (port $PORT)
set -e

PORT="${PORT:-8000}"

case "${DEPLOY_MODE:-api}" in
  mcp)
    exec env \
      STOREFRONT_MCP_HOST=0.0.0.0 \
      STOREFRONT_MCP_PORT="$PORT" \
      python3 shopping-agent/managed-agents/storefront-mcp-server/storefront_mcp_server.py
    ;;
  gemini)
    exec env \
      GEMINI_UI_PORT="$PORT" \
      python3 scripts/gemini_mcp_ui_server.py
    ;;
  *)
    exec uvicorn salesforce.api.main_production:app \
      --app-dir examples \
      --host 0.0.0.0 \
      --port "$PORT"
    ;;
esac
