"""Salesforce vertical merchant router — MerchantAgent backed by Salesforce OMS."""

from __future__ import annotations

from fastapi import APIRouter

from commerce_common.memory import MemoryStore
from demo_common import REPO_ROOT, MerchantIdentity, build_anthropic_client, build_merchant_router
from merchant_agent_runtime import MerchantAgent

from .agent_config import build_merchant_config
from .sf_oms_backend import SalesforceOMSBackend

IDENTITY = MerchantIdentity(merchant_id="salesforce-oms", operator="Salesforce Operator")


def create_merchant_router(backend: SalesforceOMSBackend, memory_store: MemoryStore) -> APIRouter:
    config = build_merchant_config(backend.store_name)
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
