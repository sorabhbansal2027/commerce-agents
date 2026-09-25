"""Entry point: merchant MCP server wired to the Salesforce OMS backend.

Deploy this to Railway (or any container host) behind HTTPS.  The server
refuses to bind off loopback unless MERCHANT_MCP_UNSAFE_ALLOW_NO_AUTH=1,
which your infrastructure sets once an authenticating gateway (Railway's
private networking, an API token header, etc.) is in front.

    MERCHANT_MCP_UNSAFE_ALLOW_NO_AUTH=1  # set by Railway env
    SF_INSTANCE_URL=https://...
    SF_CLIENT_ID=...
    SF_CLIENT_SECRET=...
    PORT=8201                            # Railway injects this

python scripts/merchant_mcp_server_sf.py

Credentials are loaded from examples/salesforce/.env (lowest priority)
then repo-root .env, then the environment (highest priority), so you can
override any value by exporting it before running.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in [str(REPO), str(REPO / "examples")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from demo_common import load_demo_env  # noqa: E402

# Load B2B org credentials from examples/salesforce/.env (env vars already
# set in the process take priority — Railway / local export wins).
load_demo_env(REPO / "examples" / "salesforce")

from merchant_agent.managed_agents.merchant_mcp_server.merchant_mcp_server import (  # noqa: E402
    build_server,
    default_config,
)
from salesforce.api.sf_oms_backend import SalesforceOMSBackend  # noqa: E402

HOST = os.environ.get("MERCHANT_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("MERCHANT_MCP_PORT", "8201")))


def main() -> None:
    cfg = default_config()
    backend = SalesforceOMSBackend()
    server = build_server(backend=backend, config=cfg, host=HOST, port=PORT)
    mcp_url = f"http://{HOST}:{PORT}/mcp"
    print(f"Merchant MCP server (Salesforce B2B backend) → {mcp_url}", flush=True)
    print(f"  SF_INSTANCE_URL = {os.environ.get('SF_INSTANCE_URL', '(not set)')}", flush=True)
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
