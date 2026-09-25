"""Salesforce vertical merchant router — MerchantAgent backed by Salesforce OMS or SFCC BM."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter

from commerce_common.memory import MemoryStore
from demo_common import REPO_ROOT, MerchantIdentity, build_anthropic_client, build_merchant_router
from merchant_agent import BusinessSnapshot, ChangeLedger, MerchantBackend, MerchantSessionContext
from merchant_agent_runtime import MerchantAgent

log = logging.getLogger(__name__)

from .agent_config import build_merchant_config

IDENTITY = MerchantIdentity(merchant_id="salesforce-oms", operator="Salesforce Operator")


class _SFCCAdapter:
    """Adapts SFCCBusinessManagerBackend to the DemoMerchant + DemoStorefront protocols.

    all_listings() returns [] because SFCC has no sync bulk-fetch; the merchant portal
    search box (which calls search_listings) covers product discovery.
    recent_orders() returns [] — SFCC BM does not expose order history.
    """

    def __init__(self, sfcc: Any) -> None:
        self._sfcc = sfcc
        self.ledger: ChangeLedger = sfcc._ledger

    @property
    def store_name(self) -> str:
        return self._sfcc._site_id

    @property
    def products(self) -> dict:
        return {}

    def all_listings(self) -> list:
        return []

    def recent_orders(self, n: int) -> list:
        return []

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        try:
            return await self._sfcc.get_business_snapshot(session, period)
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            log.warning("SFCC order_search unavailable (%s); returning empty snapshot", exc)
            return BusinessSnapshot(store_name=self.store_name)

    async def get_order_issues(self, session: MerchantSessionContext) -> list:
        try:
            return await self._sfcc.get_order_issues(session)
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            log.warning("SFCC order_search unavailable for order issues (%s)", exc)
            return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sfcc, name)


def _build_merchant_backend() -> MerchantBackend:
    if os.environ.get("SFCC_INSTANCE_URL"):
        from sfcc.sfcc_bm_backend import SFCCBusinessManagerBackend
        return _SFCCAdapter(SFCCBusinessManagerBackend())  # type: ignore[return-value]
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
