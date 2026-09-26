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
                    status="active" if h.get("online_flag", {}).get("default", True) else "paused",
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
        # order_search lives in the Shop API (/dw/shop/), not the Data API
        instance = self._sfcc._instance
        version = self._sfcc._version
        client_id = self._sfcc._client_id
        try:
            token = await self._sfcc._get_token()
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"{instance}/s/{os.environ.get('SFCC_DISPLAY_SITE_ID', 'DreamHaus')}/dw/shop/{version}/order_search",
                    json={"query": {"match_all_query": {}}, "count": 1,
                          "select": "(total,hits.(order_no,status,order_total,currency))"},
                    headers={"Authorization": f"Bearer {token}",
                             "x-dw-client-id": client_id,
                             "Content-Type": "application/json"},
                )
                r.raise_for_status()
                body = r.json()
            total_orders = body.get("total", 0)
            hits = body.get("hits", [])
            sales = sum(float(h.get("order_total", 0) or 0) for h in hits)
            return BusinessSnapshot(
                period=period or "30d", sales=sales, orders=total_orders,
                note=None,
            )
        except Exception as exc:
            log.warning("SFCC shop order_search unavailable (%s); falling back", exc)
        try:
            data = await self._sfcc._request("POST", "product_search", json={
                "query": {"match_all_query": {}}, "count": 1,
            })
            product_count = (data or {}).get("total")
        except Exception:
            product_count = None
        return BusinessSnapshot(
            period=period or "30d", sales=0.0, orders=0,
            product_count=product_count,
            note="Order history requires Trusted System auth — not available via client credentials",
        )

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
                alloc = r.get("allocation", {})
                ats = r.get("ats") or (alloc.get("amount") if isinstance(alloc, dict) else alloc) or 0
                if ats <= 5:
                    alerts.append(
                        InventoryAlert(
                            listing_id=r.get("product_id", r.get("id", "")),
                            title=r.get("product_name", r.get("product_id", r.get("id", ""))),
                            kind="low_stock",
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
            from merchant_agent import OrderIssue
            instance = self._sfcc._instance
            version = self._sfcc._version
            client_id = self._sfcc._client_id
            token = await self._sfcc._get_token()
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"{instance}/s/{os.environ.get('SFCC_DISPLAY_SITE_ID', 'DreamHaus')}/dw/shop/{version}/order_search",
                    json={"query": {"filtered_query": {
                              "query": {"match_all_query": {}},
                              "filter": {"term_filter": {"field": "status",
                                  "operator": "one_of", "values": ["failed", "hold"]}}}},
                          "select": "(**)", "count": 50},
                    headers={"Authorization": f"Bearer {token}",
                             "x-dw-client-id": client_id,
                             "Content-Type": "application/json"},
                )
                r.raise_for_status()
            hits = r.json().get("hits", [])
            return [OrderIssue(
                order_id=h.get("order_no", ""),
                issue_kind=h.get("status", "hold"),
                summary=f"Order {h.get('order_no')} is {h.get('status')}",
                customer=h.get("customer_info", {}).get("customer_name"),
                order_total=h.get("order_total"),
                currency=self._sfcc._currency,
                created_at=h.get("creation_date"),
            ) for h in hits]
        except Exception as exc:
            log.warning("SFCC shop order_search unavailable for order issues (%s)", exc)
            return []

    async def search_listings(self, session: MerchantSessionContext, query: str, filters: Any = None, limit: int = 8) -> list:
        try:
            from merchant_agent import Listing, ListingFilters
            base_query: dict[str, Any] = (
                {"match_all_query": {}}
                if not query
                else {"text_query": {"fields": ["id", "name"], "search_phrase": query}}
            )
            body: dict[str, Any] = {
                "query": base_query,
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
                    status="active" if h.get("online_flag", {}).get("default", True) else "paused",
                    price=float(price) if price else 0.0,
                    currency=self._sfcc._currency,
                    category=h.get("primary_category_id"),
                ))
            return listings
        except (httpx.HTTPStatusError, httpx.ConnectError) as exc:
            log.warning("SFCC product_search unavailable (%s)", exc)
            return []

    async def get_listing(self, session: MerchantSessionContext, listing_id: str) -> Any:
        result = await self._sfcc.get_listing(session, listing_id)
        if result is None:
            return None
        # Fix: org-level SFCC returns name as {"default": "..."} dict; also "inactive" is invalid
        raw_name = result.title if isinstance(result.title, str) else ""
        if isinstance(result.title, dict):
            raw_name = result.title.get("default", listing_id)
        from merchant_agent import ListingDetails
        return ListingDetails(
            listing_id=result.listing_id,
            title=raw_name,
            status="paused" if getattr(result, "status", "active") == "inactive" else getattr(result, "status", "active"),
            price=result.price,
            currency=result.currency,
            category=result.category,
            description=getattr(result, "description", None),
            stock=getattr(result, "stock", None),
            attributes=getattr(result, "attributes", {}),
        )

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
