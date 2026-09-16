"""Salesforce Commerce API — shopping agent (storefront) and merchant agent (portal)
both backed by Salesforce OMS via two SOQL queries.

    uvicorn salesforce.api.main:app --app-dir examples --reload --port 8005

Storefront  → http://localhost:3005
Merchant    → http://localhost:3105
"""

from __future__ import annotations

import asyncio
import logging

from commerce_common.memory import InMemoryMemoryStore
from demo_common import (
    REPO_ROOT,
    MemorySeeder,
    build_anthropic_client,
    build_storefront_host,
    load_demo_env,
)
from fastapi import Response
from fastapi.staticfiles import StaticFiles
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .merchant import create_merchant_router
from .sf_oms_backend import DATA_DIR, PRODUCT_IMAGES_DIR, SalesforceOMSBackend

log = logging.getLogger(__name__)

load_demo_env(DATA_DIR)  # loads examples/salesforce/.env then repo-root .env

backend = SalesforceOMSBackend()

# Pre-load the Salesforce product catalog synchronously at import time so that
# all_listings() (sync) is populated before the first request arrives.
# asyncio.run() is safe here because uvicorn imports the module before its own
# event loop starts.
try:
    asyncio.run(backend._ensure_products_loaded())
    log.info("SF catalog pre-loaded: %d products", len(backend._products_cache))
except Exception as _exc:
    log.warning("SF catalog pre-load failed at startup: %s", _exc)

agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
    memory_store=InMemoryMemoryStore(),
    client=build_anthropic_client(),
)

host = build_storefront_host(
    title="Salesforce Commerce demo API",
    example_root=DATA_DIR,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(DATA_DIR / "memory-seed.json"),
)
app = host.app
app.include_router(create_merchant_router(backend, InMemoryMemoryStore()), prefix="/api/merchant")
app.mount("/products", StaticFiles(directory=PRODUCT_IMAGES_DIR, check_dir=False), name="products")


@app.post("/api/reload-products")
async def reload_products() -> Response:
    """Force a fresh product catalog load from Salesforce (clears the in-memory cache)."""
    backend._products_loaded = False
    await backend._ensure_products_loaded()
    count = len(backend._products_cache)
    log.info("Product catalog reloaded: %d products", count)
    return Response(content=f'{{"products": {count}}}', media_type="application/json")
