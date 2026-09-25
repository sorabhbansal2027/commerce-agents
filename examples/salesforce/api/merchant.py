"""Salesforce vertical merchant router — MerchantAgent backed by Salesforce OMS or SFCC BM."""

from __future__ import annotations

import os

from fastapi import APIRouter

from commerce_common.memory import MemoryStore
from demo_common import REPO_ROOT, MerchantIdentity, build_anthropic_client, build_merchant_router
from merchant_agent import MerchantBackend
from merchant_agent_runtime import MerchantAgent

from .agent_config import build_merchant_config

IDENTITY = MerchantIdentity(merchant_id="salesforce-oms", operator="Salesforce Operator")


def _build_merchant_backend() -> MerchantBackend:
    if os.environ.get("SFCC_INSTANCE_URL"):
        from sfcc.sfcc_bm_backend import SFCCBusinessManagerBackend
        return SFCCBusinessManagerBackend()
    from .sf_oms_backend import SalesforceOMSBackend
    return SalesforceOMSBackend()


def create_merchant_router(backend: MerchantBackend, memory_store: MemoryStore) -> APIRouter:
    store_name = getattr(backend, "store_name", os.environ.get("SFCC_SITE_ID", "DreamHaus"))
    config = build_merchant_config(store_name)
    agent = MerchantAgent(
        backend=backend,
        skills_dir=REPO_ROOT / "merchant-agent" / "skills",
        config=config,
        memory_store=memory_store,
        client=build_anthropic_client(),
    )
    return build_merchant_router(
        storefront=backend,
        backend=backend,
        agent=agent,
        identity=IDENTITY,
        example_dir="salesforce",
    )
