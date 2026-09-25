"""Entry point: merchant MCP server wired to Salesforce Commerce Cloud (OCAPI).

Required env vars:
    SFCC_INSTANCE_URL       e.g. https://zzrl-008.sandbox.us01.dx.commercecloud.salesforce.com
    SFCC_CLIENT_ID          OCAPI client ID from BM > Global Preferences > WebService Client IDs
    SFCC_CLIENT_SECRET      client secret
    SFCC_SITE_ID            site ID shown in BM (e.g. DreamHouse)
    MERCHANT_MCP_UNSAFE_ALLOW_NO_AUTH=1   set by Railway once HTTPS gateway is in place

Optional:
    SFCC_OCAPI_VERSION          default v23_2
    SFCC_INVENTORY_LIST_ID      default RefArchInventoryM
    SFCC_PRICEBOOK_ID           default usd-m-list-prices
    SFCC_CURRENCY               default USD
    PORT / MERCHANT_MCP_PORT    default 8201

python scripts/merchant_mcp_server_sfcc.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in [str(REPO), str(REPO / "examples")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from merchant_agent.managed_agents.merchant_mcp_server.merchant_mcp_server import (  # noqa: E402
    build_server,
    default_config,
)
from sfcc.sfcc_bm_backend import SFCCBusinessManagerBackend  # noqa: E402

HOST = os.environ.get("MERCHANT_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("MERCHANT_MCP_PORT", "8201")))


def main() -> None:
    cfg = default_config()
    backend = SFCCBusinessManagerBackend(config=cfg)
    server = build_server(backend=backend, config=cfg, host=HOST, port=PORT)
    print(
        f"Merchant MCP server (SFCC OCAPI backend, site={os.environ.get('SFCC_SITE_ID')}) "
        f"→ http://{HOST}:{PORT}/mcp",
        flush=True,
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
