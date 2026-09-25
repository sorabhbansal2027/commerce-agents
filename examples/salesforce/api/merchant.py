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
        # _site_id is "-" (org-level) when SFCC_SITE_ID=-; use display name instead
        return os.environ.get("SFCC_DISPLAY_SITE_ID", "DreamHaus")

    @property
    def products(self) -> dict:
        return {}

    def all_listings(self) -> list:
        try:
            import base64, json as _json
            from merchant_agent import Listing
            # Sync HTTP — all_listings() is called without await by the router
            creds = base64.b64encode(
                f"{self._sfcc._client_id}:{self._sfcc._client_secret}".encode()
            ).decode()
            with httpx.Client(timeout=20) as c:
                tok_r = c.post(
                    "https://account.demandware.com/dwsso/oauth2/access_token",
                    headers={"Authorization": f"Basic {creds}"},
                    data={"grant_type": "client_credentials"},
                )
                tok_r.raise_for_status()
                token = tok_r.json()["access_token"]
                url = (f"{self._sfcc._instance}/s/{self._sfcc._site_id}"
                       f"/dw/data/{self._sfcc._version}/product_search")
                r = c.post(url, json={
                    "query": {"match_all_query": {}},
                    "select": "(**)", "count": 50,
                }, headers={
                    "Authorization": f"Bearer {token}",
                    "x-dw-client-id": self._sfcc._client_id,
                    "Content-Type": "application/json",
                })
                r.raise_for_status()
                hits = r.json().get("hits", [])
            listings = []
            for h in hits:
                raw_name = h.get("name", "")
                title = raw_name.get("default", "") if isinstance(raw_name, dict) else raw_name
                prices = h.get("prices", {})
                price = next(iter(prices.values()), 0.0) if prices else 0.0
                listings.append(Listing(
                    listing_id=h.get("id", ""),
                    title=title,
                    status="active" if h.get("online_flag", {}).get("default", True) else "inactive",
                    price=float(price) if price else 0.0,
                    currency=self._sfcc._currency,
                    category=h.get("primary_category_id"),
                ))
            return listings
        except Exception as exc:
            log.warning("SFCC all_listings unavailable (%s)", exc)
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
            return BusinessSnapshot(period=period or "30d", sales=0.0, orders=0,
                                   note="Order data unavailable — OCAPI order_search not configured")

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list:
        try:
            from merchant_agent import InventoryAlert
            # Use org-level access (/s/-/) with correct selector — no trailing field after (**)
            data = await self._sfcc._request(
                "GET",
                f"inventory_lists/{self._sfcc._inv_list}/product_inventory_records"
                "?select=(**)",
                site=True,
            )
            records = (data or {}).get("data", [])
            alerts = []
            for r in records:
                ats = r.get("ats", r.get("allocation", 0)) or 0
                if ats <= 5:
                    alerts.append(
                        InventoryAlert(
                            listing_id=r.get("id", ""),
                            title=r.get("id", ""),
                            alert_kind="low_stock",
                            stock=int(ats),
                            threshold=5,
                        )
                    )
            return alerts
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            log.warning("SFCC inventory OCAPI unavailable (%s)", exc)
            return []

    async def get_order_issues(self, session: MerchantSessionContext) -> list:
        try:
            return await self._sfcc.get_order_issues(session)
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            log.warning("SFCC order_search unavailable for order issues (%s)", exc)
            return []

    async def search_listings(self, session: MerchantSessionContext, query: str, filters: Any = None, limit: int = 8) -> list:
        try:
            from merchant_agent import Listing, ListingFilters
            # short_description is not queryable on this SFCC instance
            body: dict[str, Any] = {
                "query": {"text_query": {"fields": ["id", "name"], "search_phrase": query}},
                "select": "(**)",
                "count": limit,
            }
            if filters and isinstance(filters, ListingFilters) and filters.category:
                body["query"] = {"filtered_query": {"query": body["query"],
                    "filter": {"term_filter": {"field": "primary_category_id",
                        "operator": "is", "values": [filters.category]}}}}
            data = await self._sfcc._request("POST", "product_search", json=body)
            hits = (data or {}).get("hits", [])
            listings = []
            for h in hits:
                # name is a localized dict {"default": "..."} in org-level response
                raw_name = h.get("name", "")
                title = raw_name.get("default", "") if isinstance(raw_name, dict) else raw_name
                prices = h.get("prices", {})
                price = next(iter(prices.values()), 0.0) if prices else 0.0
                listings.append(Listing(
                    listing_id=h.get("id", ""),
                    title=title,
                    status="active" if h.get("online_flag", {}).get("default", True) else "inactive",
                    price=float(price) if price else 0.0,
                    currency=self._sfcc._currency,
                    category=h.get("primary_category_id"),
                ))
            return listings
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            log.warning("SFCC product_search unavailable (%s)", exc)
            return []

    async def get_pending_quote_approvals(self, session: MerchantSessionContext) -> list:
        return []  # SFCC BM does not support quote approvals

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
