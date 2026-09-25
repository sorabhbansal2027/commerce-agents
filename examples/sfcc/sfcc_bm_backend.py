"""SFCCBusinessManagerBackend — MerchantBackend wired to Salesforce Commerce Cloud
via the OCAPI Data API (v23.2+).

Authentication: OAuth2 client_credentials via Account Manager.
All reads are safe; staged writes hold in memory until apply_change makes the OCAPI
write call.

Required environment variables:
    SFCC_INSTANCE_URL   e.g. https://zzrl-008.sandbox.us01.dx.commercecloud.salesforce.com
    SFCC_CLIENT_ID      OCAPI client ID registered in BM > Global Preferences > WebService Client IDs
    SFCC_CLIENT_SECRET  client secret
    SFCC_SITE_ID        site ID as shown in BM (e.g. DreamHouse, RefArch)

Optional:
    SFCC_OCAPI_VERSION  default v23_2
    SFCC_INVENTORY_LIST_ID  default RefArchInventoryM
    SFCC_PRICEBOOK_ID       default usd-m-list-prices
    SFCC_CATALOG_ID         master catalog ID for product search
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from merchant_agent import (
    ActorKind,
    AnalysisTable,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeLedger,
    ChangeNotApplicable,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantAgentConfig,
    MerchantBackend,
    MerchantSessionContext,
    MetricPoint,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)

log = logging.getLogger(__name__)

_AM_TOKEN_URL = "https://account.demandware.com/dwsso/oauth2/access_token"


class SFCCBusinessManagerBackend(MerchantBackend):
    """MerchantBackend backed by Salesforce Commerce Cloud OCAPI Data API.

    All staged changes live in-memory until apply_change executes the OCAPI write.
    Per-connection lifetimes mean the ledger is per-session, matching the MCP server's
    one-executor-per-connection pattern.
    """

    def __init__(self, config: MerchantAgentConfig | None = None):
        self._cfg = config or MerchantAgentConfig()
        self._ledger = ChangeLedger(self._cfg)

        self._instance = os.environ["SFCC_INSTANCE_URL"].rstrip("/")
        self._client_id = os.environ["SFCC_CLIENT_ID"]
        self._client_secret = os.environ["SFCC_CLIENT_SECRET"]
        self._site_id = os.environ.get("SFCC_SITE_ID", "RefArch")
        self._version = os.environ.get("SFCC_OCAPI_VERSION", "v23_2")
        self._inv_list = os.environ.get("SFCC_INVENTORY_LIST_ID", "RefArchInventoryM")
        self._pricebook = os.environ.get("SFCC_PRICEBOOK_ID", "usd-m-list-prices")
        self._catalog = os.environ.get("SFCC_CATALOG_ID", "")
        self._currency = os.environ.get("SFCC_CURRENCY", "USD")

        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._lock = asyncio.Lock()

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _get_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._token_expiry - 30:
                return self._token
            creds = base64.b64encode(
                f"{self._client_id}:{self._client_secret}".encode()
            ).decode()
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    _AM_TOKEN_URL,
                    headers={"Authorization": f"Basic {creds}"},
                    data={"grant_type": "client_credentials"},
                )
                r.raise_for_status()
                body = r.json()
            self._token = body["access_token"]
            self._token_expiry = time.monotonic() + int(body.get("expires_in", 1800))
            return self._token

    async def _request(
        self, method: str, path: str, *, site: bool = True, **kwargs: Any
    ) -> Any:
        token = await self._get_token()
        if site:
            url = f"{self._instance}/s/{self._site_id}/dw/data/{self._version}/{path.lstrip('/')}"
        else:
            url = f"{self._instance}/dw/data/{self._version}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "x-dw-client-id": self._client_id,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.request(method, url, headers=headers, **kwargs)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json() if r.content else {}

    # ── Performance ───────────────────────────────────────────────────────────

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        days = 30 if not period or "month" in period.lower() else 7
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

        body = {
            "query": {
                "filtered_query": {
                    "query": {"match_all_query": {}},
                    "filter": {
                        "range_filter": {"field": "creation_date", "from": since}
                    },
                }
            },
            "select": "(**)",
            "count": 200,
            "sorts": [{"field": "creation_date", "sort_order": "desc"}],
        }
        data = await self._request("POST", "order_search", json=body)
        hits = (data or {}).get("hits", [])

        revenue = sum(
            h.get("order_total", 0)
            for h in hits
            if h.get("status") not in ("cancelled", "failed")
        )
        order_count = len(
            [h for h in hits if h.get("status") not in ("cancelled", "failed")]
        )
        aov = round(revenue / order_count, 2) if order_count else None

        # product count from catalog
        prod_data = await self._request(
            "GET", "products?count=1&select=total", site=True
        )
        product_count = (prod_data or {}).get("total")

        return BusinessSnapshot(
            merchant_id=session.merchant_id,
            period=period or f"last {days} days",
            revenue=round(revenue, 2),
            order_count=order_count,
            average_order_value=aov,
            product_count=product_count,
            currency=self._currency,
        )

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        days = 30
        since = datetime.now(UTC) - timedelta(days=days)

        body = {
            "query": {
                "filtered_query": {
                    "query": {"match_all_query": {}},
                    "filter": {
                        "range_filter": {
                            "field": "creation_date",
                            "from": since.strftime("%Y-%m-%dT00:00:00Z"),
                        }
                    },
                }
            },
            "select": "(**)",
            "count": 200,
        }
        data = await self._request("POST", "order_search", json=body)
        hits = [
            h
            for h in (data or {}).get("hits", [])
            if h.get("status") not in ("cancelled", "failed")
        ]

        # Aggregate by day
        by_day: dict[str, list[float]] = defaultdict(list)
        for h in hits:
            raw = h.get("creation_date", "")[:10]
            if metric in ("revenue", "sales"):
                by_day[raw].append(h.get("order_total", 0))
            elif metric == "order_count":
                by_day[raw].append(1)
            elif metric == "average_order_value":
                by_day[raw].append(h.get("order_total", 0))

        points: list[MetricPoint] = []
        for d in sorted(by_day):
            vals = by_day[d]
            if metric == "average_order_value":
                v = sum(vals) / len(vals) if vals else 0
            else:
                v = sum(vals)
            points.append(MetricPoint(date=d, value=round(v, 2)))

        return MetricSeries(
            metric=metric,
            period=period or f"last {days} days",
            granularity=granularity,
            points=points,
            currency=self._currency if metric in ("revenue", "sales", "average_order_value") else None,
        )

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        if campaign_id:
            data = await self._request("GET", f"campaigns/{campaign_id}")
            rows = [data] if data else []
        else:
            data = await self._request("GET", "campaigns?count=20")
            rows = (data or {}).get("data", [])

        result: list[Campaign] = []
        for row in rows:
            promos = row.get("promotions", [])
            result.append(
                Campaign(
                    campaign_id=row.get("id", ""),
                    name=row.get("description") or row.get("id", ""),
                    status=row.get("enabled", True) and "active" or "inactive",
                    start_date=row.get("start_date"),
                    end_date=row.get("end_date"),
                    promotion_count=len(promos),
                )
            )
        return result

    # ── Catalog ───────────────────────────────────────────────────────────────

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        body: dict[str, Any] = {
            "query": {
                "text_query": {
                    "fields": ["id", "name", "short_description"],
                    "search_phrase": query,
                }
            },
            "select": "(**)",
            "count": limit,
            "expand": ["availability", "prices"],
        }
        if filters and filters.category:
            body["query"] = {
                "filtered_query": {
                    "query": body["query"],
                    "filter": {
                        "term_filter": {
                            "field": "primary_category_id",
                            "operator": "is",
                            "values": [filters.category],
                        }
                    },
                }
            }

        data = await self._request("POST", "product_search", json=body)
        hits = (data or {}).get("hits", [])

        listings: list[Listing] = []
        for h in hits:
            rep = h.get("represented_product", h)
            prices = rep.get("prices", {})
            price = next(iter(prices.values()), None) if prices else None
            listings.append(
                Listing(
                    listing_id=rep.get("id", h.get("product_id", "")),
                    title=rep.get("name", ""),
                    status="active" if rep.get("online", True) else "inactive",
                    price=price,
                    currency=self._currency,
                    category=rep.get("primary_category_id"),
                )
            )
        return listings

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        data = await self._request(
            "GET",
            f"products/{listing_id}?expand=availability,prices,categories,variations",
        )
        if not data:
            return None
        prices = data.get("price_per_currency", []) or []
        price_map = {p.get("currency"): p.get("price") for p in prices}
        price = price_map.get(self._currency) or next(iter(price_map.values()), None)

        stock: int | None = None
        avail = data.get("inventory", {})
        if avail:
            stock = avail.get("ats") or avail.get("allocation")

        return ListingDetails(
            listing_id=data.get("id", listing_id),
            title=data.get("name", ""),
            status="active" if data.get("online", True) else "inactive",
            price=price,
            currency=self._currency,
            category=data.get("primary_category_id"),
            description=data.get("short_description") or data.get("long_description"),
            stock=stock,
            attributes={
                k: v
                for k, v in data.get("c_", {}).items()
                if isinstance(v, (str, int, float, bool))
            } if data.get("c_") else {},
        )

    # ── Inventory and order health ────────────────────────────────────────────

    async def get_inventory_alerts(
        self, session: MerchantSessionContext
    ) -> list[InventoryAlert]:
        data = await self._request(
            "GET",
            f"inventory_lists/{self._inv_list}/product_inventory_records"
            "?select=(**),availability&count=200",
        )
        records = (data or {}).get("data", [])

        alerts: list[InventoryAlert] = []
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

    async def get_order_issues(
        self, session: MerchantSessionContext
    ) -> list[OrderIssue]:
        body = {
            "query": {
                "filtered_query": {
                    "query": {"match_all_query": {}},
                    "filter": {
                        "term_filter": {
                            "field": "status",
                            "operator": "one_of",
                            "values": ["failed", "hold"],
                        }
                    },
                }
            },
            "select": "(**)",
            "count": 50,
        }
        data = await self._request("POST", "order_search", json=body)
        hits = (data or {}).get("hits", [])

        issues: list[OrderIssue] = []
        for h in hits:
            issues.append(
                OrderIssue(
                    order_id=h.get("order_no", ""),
                    issue_kind=h.get("status", "hold"),
                    summary=f"Order {h.get('order_no')} is {h.get('status')}",
                    customer=h.get("customer_info", {}).get("customer_name"),
                    order_total=h.get("order_total"),
                    currency=self._currency,
                    created_at=h.get("creation_date"),
                )
            )
        return issues

    # ── Pricing ───────────────────────────────────────────────────────────────

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        pb_data = await self._request(
            "GET",
            f"pricebooks/{self._pricebook}/price-tables/{listing_id}",
            site=False,
        )
        if not pb_data:
            return None
        entries = pb_data.get("price_infos", pb_data.get("data", []))
        current_price: float | None = None
        if entries:
            current_price = entries[0].get("price") if isinstance(entries, list) else pb_data.get("price")

        return PricingContext(
            listing_id=listing_id,
            current_price=current_price,
            currency=self._currency,
            pricebook_id=self._pricebook,
        )

    # ── Staged writes ─────────────────────────────────────────────────────────

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        items = [
            ChangeItem(target=listing_id, field=k, before=None, after=str(v))
            for k, v in fields.items()
        ]
        return self._ledger.stage(
            kind=ChangeKind.LISTING_UPDATE,
            summary=f"Update listing {listing_id}: {', '.join(fields)}",
            items=items,
            actor=session.operator,
            guardrail_notes=[note] if note else [],
        )

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = [
            ChangeItem(
                target=i.listing_id,
                field="price",
                before=str(i.current_price) if i.current_price is not None else None,
                after=str(i.new_price),
            )
            for i in items
        ]
        return self._ledger.stage(
            kind=ChangeKind.PRICE_UPDATE,
            summary=f"Price update for {len(items)} item(s)",
            items=change_items,
            actor=session.operator,
            currency=self._currency,
            guardrail_notes=[note] if note else [],
        )

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = [
            ChangeItem(
                target=i.listing_id,
                field=i.action or "allocation",
                before=None,
                after=str(i.quantity) if i.quantity is not None else i.action,
            )
            for i in items
        ]
        return self._ledger.stage(
            kind=ChangeKind.INVENTORY_ACTION,
            summary=f"Inventory action for {len(items)} item(s)",
            items=change_items,
            actor=session.operator,
            guardrail_notes=[note] if note else [],
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        items = [
            ChangeItem(
                target=lid,
                field="discount_pct",
                before=None,
                after=str(promotion.discount_pct),
            )
            for lid in (promotion.listing_ids or [])
        ]
        if not items:
            items = [
                ChangeItem(
                    target="store",
                    field="promotion",
                    before=None,
                    after=promotion.name,
                )
            ]
        return self._ledger.stage(
            kind=ChangeKind.PROMOTION,
            summary=f"Promotion '{promotion.name}' {promotion.discount_pct}% off",
            items=items,
            actor=session.operator,
            currency=self._currency,
        )

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        return self._ledger.stage(
            kind=ChangeKind.CAMPAIGN,
            summary=f"Campaign '{campaign.name}'",
            items=[
                ChangeItem(
                    target=campaign.campaign_id or "new",
                    field="campaign",
                    before=None,
                    after=campaign.name,
                )
            ],
            actor=session.operator,
        )

    async def get_pending_changes(
        self, session: MerchantSessionContext
    ) -> list[StagedChange]:
        return self._ledger.pending()

    # ── Apply / discard ───────────────────────────────────────────────────────

    async def apply_change(
        self, session: MerchantSessionContext, change_id: str
    ) -> StagedChange:
        change = self._ledger.get(change_id)
        if not change:
            raise ChangeNotApplicable(f"change {change_id} not found")

        if change.kind is ChangeKind.PRICE_UPDATE:
            for item in change.items:
                await self._request(
                    "PUT",
                    f"pricebooks/{self._pricebook}/price-tables/{item.target}",
                    site=False,
                    json={"price": float(item.after), "currency_mnemonic": self._currency},
                )

        elif change.kind is ChangeKind.INVENTORY_ACTION:
            for item in change.items:
                payload: dict[str, Any] = {}
                if item.field == "allocation":
                    payload["allocation"] = int(item.after)
                elif item.field in ("in_stock", "perpetual"):
                    payload["perpetual"] = item.after.lower() == "true"
                else:
                    payload["allocation"] = int(item.after) if item.after and item.after.isdigit() else 0
                await self._request(
                    "PATCH",
                    f"inventory_lists/{self._inv_list}/product_inventory_records/{item.target}",
                    json=payload,
                )

        elif change.kind is ChangeKind.LISTING_UPDATE:
            for item in change.items:
                await self._request(
                    "PATCH",
                    f"products/{item.target}",
                    json={item.field: item.after},
                )

        elif change.kind is ChangeKind.PROMOTION:
            promo_id = f"promo-{uuid.uuid4().hex[:8]}"
            listing_ids = [i.target for i in change.items if i.target != "store"]
            promo_payload: dict[str, Any] = {
                "id": promo_id,
                "name": change.summary,
                "promotion_class": "product",
                "enabled_flag": True,
            }
            if listing_ids:
                promo_payload["product_ids"] = listing_ids
            await self._request("POST", "promotions", json=promo_payload)

        elif change.kind is ChangeKind.CAMPAIGN:
            item = change.items[0]
            if item.target and item.target != "new":
                await self._request(
                    "PATCH",
                    f"campaigns/{item.target}",
                    json={"description": change.summary, "enabled": True},
                )
            else:
                camp_id = f"camp-{uuid.uuid4().hex[:8]}"
                await self._request(
                    "POST",
                    "campaigns",
                    json={"id": camp_id, "description": change.summary, "enabled": True},
                )

        return self._ledger.apply(change_id, session.operator)

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        change = self._ledger.get(change_id)
        if not change:
            raise ChangeNotApplicable(f"change {change_id} not found")
        return self._ledger.discard(change_id, session.operator, actor_kind)

    # ── Merchant context ──────────────────────────────────────────────────────

    async def get_merchant_context(
        self, session: MerchantSessionContext
    ) -> dict[str, Any] | None:
        return {
            "store": self._site_id,
            "platform": "Salesforce Commerce Cloud",
            "currency": self._currency,
            "pricebook": self._pricebook,
            "inventory_list": self._inv_list,
        }
