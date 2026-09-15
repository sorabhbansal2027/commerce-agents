"""ETSOMSBackend: MerchantBackend wired to Salesforce OMS via two SOQL queries.

  Account query  → profile count (get_business_snapshot note field)
  OrderSummary   → order count + revenue total + daily time series

Credentials are read from environment variables at startup (see .env.example).
The Salesforce Connected App uses the client_credentials OAuth2 flow — no
per-user token is needed because this is a merchant (back-office) agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.parse
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

from merchant_agent import (
    ActorKind,
    AlertCounts,
    AnalysisTable,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
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
from shopping_agent import Cart, Order, OrderItem, OrderStatus, Policy, Product, ProductDetails, SearchFilters, ShoppingSessionContext, UserPreferences

DATA_DIR = Path(__file__).parent.parent  # examples/ets/
STORE_NAME = "Salesforce"

# ---------------------------------------------------------------------------
# SOQL query templates — parameterised by ISO-8601 timestamp
# ---------------------------------------------------------------------------

_PROFILES_QUERY = """
SELECT Id, LastName, CreatedDate, CreatedBy.Name
FROM Account
WHERE CreatedDate > {since}
ORDER BY CreatedDate DESC
LIMIT 15
""".strip()

_ORDERS_QUERY = """
SELECT Id, OrderNumber, CreatedDate, GrandTotalAmount, Status
FROM OrderSummary
WHERE CreatedDate > {since}
ORDER BY CreatedDate DESC
LIMIT 500
""".strip()

# Fallback for B2B Commerce orgs where OrderSummary (OMS) is not enabled.
# B2B Commerce checkout creates standard Order records instead.
_B2B_ORDERS_QUERY = """
SELECT Id, OrderNumber, CreatedDate, TotalAmount, Status
FROM Order
WHERE CreatedDate > {since}
ORDER BY CreatedDate DESC
LIMIT 500
""".strip()

# Default "since" date — the date from the original requirement
_DEFAULT_SINCE = "2026-08-18T02:36:42.000Z"

# Active products with their standard pricebook price, ordered by name.
# Fetched once and cached; call _ensure_products_loaded() before use.
_PRODUCTS_QUERY = """
SELECT Product2Id, Product2.Name, Product2.Description, Product2.Family,
       Product2.ProductCode, UnitPrice
FROM PricebookEntry
WHERE IsActive = true
AND Product2.IsActive = true
ORDER BY Product2.Name
LIMIT 200
""".strip()


def _since_ts(period: str | None) -> str:
    """Convert a period hint to a SOQL-ready ISO-8601 timestamp.

    Handles: None, "YYYY-MM-DD", "YYYY-MM-DDTHH:MM:SS.sssZ",
    range strings like "2026-08-18 to 2026-09-09" or "2026-08-18/2026-09-09"
    (always uses the start date), and natural-language expressions like
    "last_7_days", "last_30_days", "last_week", "last_month", "this_month".
    """
    import re

    if not period:
        return _DEFAULT_SINCE

    now = datetime.now(UTC)
    p = period.strip().lower().replace(" ", "_")

    # "last_N_days" / "last_N_day"
    m = re.match(r'^last_(\d+)_days?$', p)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # "last_N_weeks"
    m = re.match(r'^last_(\d+)_weeks?$', p)
    if m:
        return (now - timedelta(weeks=int(m.group(1)))).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # "last_N_months" — approximate as 30*N days
    m = re.match(r'^last_(\d+)_months?$', p)
    if m:
        return (now - timedelta(days=30 * int(m.group(1)))).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    simple = {
        "last_week":  timedelta(weeks=1),
        "last_month": timedelta(days=30),
        "last_quarter": timedelta(days=90),
        "last_year":  timedelta(days=365),
        "this_week":  timedelta(days=now.weekday()),
    }
    if p in simple:
        return (now - simple[p]).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if p == "this_month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

    # Normalize any range separator to extract the start date
    for sep in (" to ", "/", " - ", "–"):
        if sep in period:
            period = period.split(sep)[0].strip()
            break
    if len(period) == 10:  # "YYYY-MM-DD"
        return f"{period}T00:00:00.000Z"
    # Already a full ISO timestamp
    return period


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class SalesforceOMSBackend(MerchantBackend):
    """Salesforce OMS merchant backend for ETSAgent.

    Wired methods:
      get_business_snapshot  — Account profile count + OrderSummary totals
      query_metrics          — OrderSummary revenue/count bucketed by day

    All stage/write methods raise ChangeNotApplicable (analytics-only agent).
    """

    store_name = STORE_NAME
    # DemoStorefront.products — empty because ETSAgent has no shopping catalog
    products: dict[str, ProductDetails] = {}

    def __init__(self) -> None:
        # Credentials are read lazily at first SOQL call so the server can
        # start (and pass the health check) even before .env is filled in.
        self._base = os.environ.get("SF_INSTANCE_URL", "").rstrip("/")
        self._client_id = os.environ.get("SF_CLIENT_ID", "")
        self._client_secret = os.environ.get("SF_CLIENT_SECRET", "")
        self._token: str = ""
        self._token_expires: float = 0.0
        self._token_lock = asyncio.Lock()
        self._recent_orders_cache: list[Order] = []
        self._products_cache: dict[str, Listing] = {}
        self._code_to_id: dict[str, str] = {}  # ProductCode → Product2Id
        self._products_loaded: bool = False
        self._webstore_id: str = ""  # resolved lazily from WebStore SOQL
        self._account_cache: dict[str, str] = {}  # sf_user_id → AccountId

        # ChangeLedger is required by the DemoMerchant protocol and the
        # merchant router's /overview endpoint.
        self.ledger = ChangeLedger(MerchantAgentConfig(brand_name=STORE_NAME))

    # ------------------------------------------------------------------
    # Salesforce auth — client credentials OAuth2 flow
    # ------------------------------------------------------------------

    async def _token_headers(self) -> dict[str, str]:
        """Return Authorization headers, refreshing the token when expired."""
        if not all([self._base, self._client_id, self._client_secret]):
            raise RuntimeError(
                "Salesforce credentials not configured. "
                "Set SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET in examples/ets/.env"
            )
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return {"Authorization": f"Bearer {self._token}"}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._base}/services/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                if resp.status_code >= 400:
                    log.error(
                        "SF OAuth token request failed: HTTP %s\nURL: %s/services/oauth2/token\n"
                        "client_id prefix: %s...\nResponse: %s",
                        resp.status_code,
                        self._base,
                        self._client_id[:8] if self._client_id else "(empty)",
                        resp.text,
                    )
                resp.raise_for_status()
                data = resp.json()
            self._token = data["access_token"]
            # Expire 60 s before the reported expiry to avoid clock skew.
            self._token_expires = time.monotonic() + data.get("expires_in", 3600) - 60
        return {"Authorization": f"Bearer {self._token}"}

    async def _soql(self, query: str) -> list[dict[str, Any]]:
        """Run a SOQL query and follow all nextRecordsUrl pages."""
        headers = await self._token_headers()
        url: str | None = (
            f"{self._base}/services/data/v62.0/query"
            f"?q={urllib.parse.quote(query)}"
        )
        records: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                body = resp.json()
                records.extend(body.get("records", []))
                next_path = body.get("nextRecordsUrl")
                url = f"{self._base}{next_path}" if next_path else None
        return records

    # ------------------------------------------------------------------
    # B2B Commerce helpers — WebStore ID + buyer account resolution
    # ------------------------------------------------------------------

    async def _ensure_webstore_id(self) -> str:
        """Resolve the WebStore ID once from SOQL; cache for the process lifetime."""
        if self._webstore_id:
            return self._webstore_id
        rows = await self._soql("SELECT Id FROM WebStore LIMIT 1")
        if not rows:
            raise RuntimeError("No WebStore found in this org")
        self._webstore_id = rows[0]["Id"]
        return self._webstore_id

    async def _account_id_for_user(self, sf_user_id: str) -> str | None:
        """Return the AccountId associated with a Salesforce User record.
        Returns None for non-SF IDs (e.g. 'demo-user') so the cart call
        proceeds without an effectiveAccountId."""
        if not sf_user_id or not sf_user_id.startswith("005") or len(sf_user_id) not in (15, 18):
            return None
        if sf_user_id in self._account_cache:
            return self._account_cache[sf_user_id]
        rows = await self._soql(
            f"SELECT AccountId FROM User WHERE Id = '{sf_user_id}' LIMIT 1"
        )
        if not rows or not rows[0].get("AccountId"):
            return None
        account_id = rows[0]["AccountId"]
        self._account_cache[sf_user_id] = account_id
        return account_id

    async def _b2b_request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> dict[str, Any]:
        """Make an authenticated B2B Commerce REST API call."""
        headers = await self._token_headers()
        url = f"{self._base}/services/data/v62.0{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            if resp.is_error:
                log.error("B2B API %s %s → %s: %s", method, path, resp.status_code, resp.text[:500])
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    # ------------------------------------------------------------------
    # Product catalog — loaded once from PricebookEntry + Product2
    # ------------------------------------------------------------------

    async def _ensure_products_loaded(self) -> None:
        if self._products_loaded:
            return
        records = await self._soql(_PRODUCTS_QUERY)
        cache: dict[str, Listing] = {}
        code_to_id: dict[str, str] = {}
        for r in records:
            pid = r.get("Product2Id", "")
            if not pid or pid in cache:
                continue
            p2 = r.get("Product2") or {}
            name = p2.get("Name") or pid
            family = p2.get("Family") or None
            desc = p2.get("Description") or None
            code = p2.get("ProductCode") or ""
            price = float(r.get("UnitPrice") or 0)
            cache[pid] = Listing(
                listing_id=pid,
                title=name,
                price=price,
                currency="USD",
                stock=0,
                category=family,
                status="active",
                short_description=desc,
                content_quality="good" if desc else "needs_work",
            )
            if code:
                code_to_id[code] = pid
        self._products_cache = cache
        self._code_to_id = code_to_id
        # Also populate the DemoStorefront.products dict so the /api/products
        # endpoint and the health check's product count reflect the real catalog.
        self.products = {
            pid: self._listing_to_product_details(listing)
            for pid, listing in cache.items()
        }
        self._products_loaded = True

    def _listing_to_product_details(self, listing: Listing) -> ProductDetails:
        return ProductDetails(
            product_id=listing.listing_id,
            title=listing.title,
            price=listing.price,
            currency=listing.currency or "USD",
            category=listing.category,
            short_description=listing.short_description,
            long_description=listing.short_description,
            in_stock=listing.status == "active",
        )

    # ------------------------------------------------------------------
    # Shopping catalog — delegates to the PricebookEntry product cache
    # ------------------------------------------------------------------

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        await self._ensure_products_loaded()
        results = list(self.products.values())
        if query:
            q = query.lower()
            results = [
                p for p in results
                if q in p.title.lower()
                or (p.category and q in p.category.lower())
                or (p.short_description and q in p.short_description.lower())
            ]
        if filters:
            if filters.category:
                cat = filters.category.lower()
                results = [p for p in results if p.category and cat in p.category.lower()]
            if filters.min_price is not None:
                results = [p for p in results if p.price >= filters.min_price]
            if filters.max_price is not None:
                results = [p for p in results if p.price <= filters.max_price]
        return results[:limit]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        await self._ensure_products_loaded()
        return self.products.get(product_id)

    # ------------------------------------------------------------------
    # Performance — the two wired SOQL queries
    # ------------------------------------------------------------------

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        """Run the Account + OrderSummary SOQL queries concurrently.

        - orders / sales come from OrderSummary
        - profile count goes in the note field (supplementary context)
        """
        since = _since_ts(period)
        results = await asyncio.gather(
            self._soql(_PROFILES_QUERY.format(since=since)),
            self._soql(_ORDERS_QUERY.format(since=since)),
            return_exceptions=True,
        )
        profiles_raw = results[0] if isinstance(results[0], list) else []
        orders_raw = results[1] if isinstance(results[1], list) else []
        if isinstance(results[0], Exception):
            log.warning("profiles query failed: %s", results[0])
        if isinstance(results[1], Exception):
            log.warning("orders query failed: %s", results[1])
        # Pre-warm the product cache in the background while we build the snapshot.
        if not self._products_loaded:
            asyncio.ensure_future(self._ensure_products_loaded())

        # If OrderSummary (OMS) returned nothing, fall back to the standard
        # Order object used by B2B Commerce checkout.
        use_b2b_order = not orders_raw
        if use_b2b_order:
            orders_raw = await self._soql(_B2B_ORDERS_QUERY.format(since=since))

        amount_field = "TotalAmount" if use_b2b_order else "GrandTotalAmount"

        profile_count = len(profiles_raw)
        order_count = len(orders_raw)
        total_revenue = sum(float(r.get(amount_field) or 0) for r in orders_raw)
        aov = round(total_revenue / order_count, 2) if order_count else None
        fulfilled_statuses = (
            ("Activated", "Completed") if use_b2b_order else ("Fulfilled", "Approved")
        )
        fulfilled = sum(1 for r in orders_raw if r.get("Status") in fulfilled_statuses)
        conversion_rate = round(fulfilled / order_count * 100, 1) if order_count else None

        # Fetch line items for the 6 most-recent orders.
        recent_six = orders_raw[:6]
        recent_ids = [r.get("Id", r.get("id", "")) for r in recent_six if r.get("Id") or r.get("id")]
        items_by_order: dict[str, list[OrderItem]] = {oid: [] for oid in recent_ids}
        if recent_ids:
            id_list = "','".join(recent_ids)
            if use_b2b_order:
                line_items_raw = await self._soql(
                    f"SELECT OrderId, Product2.ProductCode, Product2.Name, Quantity, UnitPrice "
                    f"FROM OrderItem WHERE OrderId IN ('{id_list}')"
                )
                for li in line_items_raw:
                    oid = li.get("OrderId", "")
                    if oid not in items_by_order:
                        continue
                    p2 = li.get("Product2") or {}
                    items_by_order[oid].append(
                        OrderItem(
                            product_id=p2.get("ProductCode") or oid,
                            title=p2.get("Name") or p2.get("ProductCode") or "Item",
                            quantity=int(float(li.get("Quantity") or 1)),
                            price=float(li.get("UnitPrice") or 0),
                        )
                    )
            else:
                line_items_raw = await self._soql(
                    f"SELECT OrderSummaryId, ProductCode, Description, Quantity, UnitPrice "
                    f"FROM OrderItemSummary WHERE OrderSummaryId IN ('{id_list}')"
                )
                for li in line_items_raw:
                    oid = li.get("OrderSummaryId", "")
                    if oid not in items_by_order:
                        continue
                    items_by_order[oid].append(
                        OrderItem(
                            product_id=li.get("ProductCode") or oid,
                            title=li.get("Description") or li.get("ProductCode") or "Item",
                            quantity=int(float(li.get("Quantity") or 1)),
                            price=float(li.get("UnitPrice") or 0),
                        )
                    )

        # Cache the 6 most-recent orders so recent_orders() (sync) can serve them.
        self._recent_orders_cache = [
            Order(
                order_id=r.get("OrderNumber") or r.get("Id", r.get("id", "")),
                status=OrderStatus.DELIVERED,
                placed_at=datetime.fromisoformat(
                    (r.get("CreatedDate") or "2026-01-01T00:00:00.000Z").replace("Z", "+00:00")
                ),
                items=items_by_order.get(r.get("Id", r.get("id", "")), []),
                total=float(r.get(amount_field) or 0),
                currency="USD",
            )
            for r in recent_six
        ]

        return BusinessSnapshot(
            period=f"since {since[:10]}",
            sales=round(total_revenue, 2),
            orders=order_count,
            average_order_value=aov,
            conversion_rate=conversion_rate,
            currency="USD",
            alerts=AlertCounts(),
            note=(
                f"{profile_count} accounts created since {since[:10]}. "
                f"Conversion shows order fulfillment rate."
            ),
        )

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        """Return daily revenue or order-count series from OrderSummary (falls back to Order)."""
        since = _since_ts(period)
        orders_raw = await self._soql(_ORDERS_QUERY.format(since=since))
        use_b2b_order = not orders_raw
        if use_b2b_order:
            orders_raw = await self._soql(_B2B_ORDERS_QUERY.format(since=since))
        amount_field = "TotalAmount" if use_b2b_order else "GrandTotalAmount"

        # Bucket records by date (YYYY-MM-DD)
        buckets: dict[str, float] = defaultdict(float)
        for r in orders_raw:
            day = (r.get("CreatedDate") or "")[:10]
            if not day:
                continue
            if metric == "order_count":
                buckets[day] += 1
            else:
                buckets[day] += float(r.get(amount_field) or 0)

        points = [
            MetricPoint(date=d, value=round(v, 2))
            for d, v in sorted(buckets.items())
        ]
        source = "Order" if use_b2b_order else "OrderSummary"
        return MetricSeries(
            metric="order_count" if metric == "order_count" else "revenue",
            unit="orders" if metric == "order_count" else "USD",
            granularity="day",
            period=f"since {since[:10]}",
            points=points,
            note=f"{len(orders_raw)} {source} records (LIMIT 500 per query)",
        )

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        return []

    # ------------------------------------------------------------------
    # Catalog — backed by Product2 + PricebookEntry SOQL
    # ------------------------------------------------------------------

    def all_listings(self) -> list[Listing]:
        return list(self._products_cache.values())

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str = "",
        filters: ListingFilters | None = None,
        limit: int = 50,
    ) -> list[Listing]:
        await self._ensure_products_loaded()
        results = list(self._products_cache.values())
        if query:
            q = query.lower()
            results = [
                l for l in results
                if q in l.title.lower()
                or (l.category and q in l.category.lower())
                or (l.short_description and q in l.short_description.lower())
            ]
        if filters:
            if filters.status:
                results = [l for l in results if l.status == filters.status]
            if filters.category:
                cat = filters.category.lower()
                results = [l for l in results if l.category and cat in l.category.lower()]
            if filters.content_quality:
                results = [l for l in results if l.content_quality == filters.content_quality]
        return results[:limit]

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        await self._ensure_products_loaded()
        listing = self._products_cache.get(listing_id)
        if not listing:
            return None
        return ListingDetails(**listing.model_dump())

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        return None

    # ------------------------------------------------------------------
    # Inventory and order health — stubs
    # ------------------------------------------------------------------

    async def get_inventory_alerts(
        self, session: MerchantSessionContext
    ) -> list[InventoryAlert]:
        await self._ensure_products_loaded()
        # Top-selling products by order line items. Try OrderItemSummary (OMS) first,
        # fall back to OrderItem (B2B Commerce).
        rows = await self._soql(
            "SELECT ProductCode, Description, COUNT(Id) total "
            "FROM OrderItemSummary "
            "WHERE ProductCode != '001' "
            "GROUP BY ProductCode, Description "
            "ORDER BY COUNT(Id) DESC LIMIT 6"
        )
        if not rows:
            rows = await self._soql(
                "SELECT Product2.ProductCode, Product2.Name, COUNT(Id) total "
                "FROM OrderItem "
                "WHERE Product2.ProductCode != '001' "
                "GROUP BY Product2.ProductCode, Product2.Name "
                "ORDER BY COUNT(Id) DESC LIMIT 6"
            )
            rows = [
                {
                    "ProductCode": (r.get("Product2") or {}).get("ProductCode", ""),
                    "Description": (r.get("Product2") or {}).get("Name", ""),
                    "total": r.get("total", 0),
                }
                for r in rows
            ]
        alerts: list[InventoryAlert] = []
        seen_ids: set[str] = set()
        for row in rows:
            code = row.get("ProductCode") or ""
            if not code:
                continue
            title = row.get("Description") or code
            lid = self._code_to_id.get(code) or code
            if not lid or lid in seen_ids:
                continue
            seen_ids.add(lid)
            sales = int(row.get("total") or 0)
            alerts.append(
                InventoryAlert(
                    listing_id=lid,
                    title=title,
                    kind="low_stock",
                    stock=0,
                    threshold=5,
                    sales_last_30d=sales,
                    storefront_visible=True,
                )
            )
        return alerts

    async def get_order_issues(
        self, session: MerchantSessionContext
    ) -> list[OrderIssue]:
        # Orders with a $0 total — likely test/data anomalies needing review.
        # Try OrderSummary (OMS) first; fall back to Order (B2B Commerce).
        rows = await self._soql(
            "SELECT Id, OrderNumber, CreatedDate, GrandTotalAmount "
            "FROM OrderSummary WHERE GrandTotalAmount = 0 "
            "ORDER BY CreatedDate DESC LIMIT 6"
        )
        if not rows:
            rows = await self._soql(
                "SELECT Id, OrderNumber, CreatedDate, TotalAmount "
                "FROM Order WHERE TotalAmount = 0 "
                "ORDER BY CreatedDate DESC LIMIT 6"
            )
        issues: list[OrderIssue] = []
        for row in rows:
            order_num = row.get("OrderNumber") or row.get("Id", "")
            created = row.get("CreatedDate") or "2026-01-01T00:00:00.000Z"
            issues.append(
                OrderIssue(
                    issue_id=f"zero-{order_num}",
                    order_id=order_num,
                    kind="buyer_message",
                    summary=f"Order {order_num} has a $0 total — check for missing payment or data issue.",
                    opened_at=datetime.fromisoformat(created.replace("Z", "+00:00")),
                )
            )
        return issues

    # ------------------------------------------------------------------
    # Staged changes — ETSAgent is analytics-only; all writes refused
    # ------------------------------------------------------------------

    def _not_applicable(self, what: str) -> ChangeNotApplicable:
        return ChangeNotApplicable(
            f"ETSAgent is analytics-only. {what} is not supported via this agent."
        )

    async def stage_listing_update(self, session, listing_id, fields, note=None) -> StagedChange:
        raise self._not_applicable("Listing updates")

    async def stage_price_update(self, session, items, note=None) -> StagedChange:
        raise self._not_applicable("Price updates")

    async def stage_inventory_action(self, session, items, note=None) -> StagedChange:
        raise self._not_applicable("Inventory actions")

    async def stage_promotion(self, session, promotion: PromotionDraft) -> StagedChange:
        raise self._not_applicable("Promotions")

    async def stage_campaign(self, session, campaign: CampaignDraft) -> StagedChange:
        raise self._not_applicable("Campaign staging")

    async def get_pending_changes(
        self, session: MerchantSessionContext
    ) -> list[StagedChange]:
        return self.ledger.pending()

    async def apply_change(
        self, session: MerchantSessionContext, change_id: str
    ) -> StagedChange:
        return self.ledger.apply(change_id, session.operator)

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        return self.ledger.discard(change_id, session.operator, actor_kind)

    # ------------------------------------------------------------------
    # DemoStorefront protocol — minimal stubs so build_merchant_router works
    # ------------------------------------------------------------------

    def product(self, product_id: str) -> ProductDetails | None:
        return self.products.get(product_id)

    def reset_session(self, session_id: str) -> None:
        pass

    def recent_orders(self, limit: int = 6) -> list[Order]:
        # Populated after the first get_business_snapshot() call (async SOQL result cache).
        return self._recent_orders_cache[:limit]

    async def get_account_context(self, session: ShoppingSessionContext) -> dict | None:
        return None

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        try:
            account_id = await self._account_id_for_user(session.user_id)
            if not account_id:
                return Cart()
            webstore_id = await self._ensure_webstore_id()
            data = await self._b2b_request(
                "GET",
                f"/commerce/webstores/{webstore_id}/carts/active",
                params={"effectiveAccountId": account_id},
            )
            return self._parse_b2b_cart(data)
        except Exception:
            log.exception("get_cart failed; returning empty cart")
            return Cart()

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            raise ValueError(
                "Cannot add to cart: no buyer account found for this user. "
                "Please ensure you are logged in as a B2B Commerce community user."
            )
        webstore_id = await self._ensure_webstore_id()
        await self._b2b_request(
            "POST",
            f"/commerce/webstores/{webstore_id}/carts/active/cart-items",
            params={"effectiveAccountId": account_id},
            json={"productId": product_id, "quantity": str(quantity), "type": "Product"},
        )
        return await self.get_cart(session)

    def _parse_b2b_cart(self, data: dict[str, Any]) -> Cart:
        """Convert B2B Commerce cart API response to Cart."""
        from shopping_agent import CartItem
        items = []
        for entry in data.get("cartItems", {}).get("records", []):
            cart_product = entry.get("cartItem", entry)
            product_id = cart_product.get("productId", "")
            name = cart_product.get("name", product_id)
            price = float(cart_product.get("unitAdjustedPrice") or cart_product.get("listPrice") or 0)
            qty = int(float(cart_product.get("quantity") or 1))
            items.append(CartItem(product_id=product_id, title=name, price=price, quantity=qty))
        currency = data.get("currencyIsoCode", "USD")
        return Cart(items=items, currency=currency)

    # ------------------------------------------------------------------
    # Storefront orders — fetched live from OrderSummary + OrderItemSummary
    # ------------------------------------------------------------------

    async def _fetch_recent_orders(self, limit: int = 20) -> list[Order]:
        orders_raw = await self._soql(
            f"SELECT Id, OrderNumber, CreatedDate, GrandTotalAmount "
            f"FROM OrderSummary ORDER BY CreatedDate DESC LIMIT {limit}"
        )
        use_b2b_order = not orders_raw
        if use_b2b_order:
            orders_raw = await self._soql(
                f"SELECT Id, OrderNumber, CreatedDate, TotalAmount "
                f"FROM Order ORDER BY CreatedDate DESC LIMIT {limit}"
            )
        if not orders_raw:
            return []
        recent_ids = [r.get("Id", "") for r in orders_raw if r.get("Id")]
        id_list = "','".join(recent_ids)
        items_by_order: dict[str, list[OrderItem]] = {oid: [] for oid in recent_ids}
        if use_b2b_order:
            line_items_raw = await self._soql(
                f"SELECT OrderId, Product2.ProductCode, Product2.Name, Quantity, UnitPrice "
                f"FROM OrderItem WHERE OrderId IN ('{id_list}')"
            )
            for li in line_items_raw:
                oid = li.get("OrderId", "")
                if oid not in items_by_order:
                    continue
                p2 = li.get("Product2") or {}
                items_by_order[oid].append(
                    OrderItem(
                        product_id=p2.get("ProductCode") or oid,
                        title=p2.get("Name") or p2.get("ProductCode") or "Item",
                        quantity=int(float(li.get("Quantity") or 1)),
                        price=float(li.get("UnitPrice") or 0),
                    )
                )
            amount_field = "TotalAmount"
        else:
            line_items_raw = await self._soql(
                f"SELECT OrderSummaryId, ProductCode, Description, Quantity, UnitPrice "
                f"FROM OrderItemSummary WHERE OrderSummaryId IN ('{id_list}')"
            )
            for li in line_items_raw:
                oid = li.get("OrderSummaryId", "")
                if oid not in items_by_order:
                    continue
                items_by_order[oid].append(
                    OrderItem(
                        product_id=li.get("ProductCode") or oid,
                        title=li.get("Description") or li.get("ProductCode") or "Item",
                        quantity=int(float(li.get("Quantity") or 1)),
                        price=float(li.get("UnitPrice") or 0),
                    )
                )
            amount_field = "GrandTotalAmount"
        result = []
        for r in orders_raw:
            oid = r.get("Id", "")
            result.append(
                Order(
                    order_id=r.get("OrderNumber") or oid,
                    status=OrderStatus.DELIVERED,
                    placed_at=datetime.fromisoformat(
                        (r.get("CreatedDate") or "2026-01-01T00:00:00.000Z").replace("Z", "+00:00")
                    ),
                    items=items_by_order.get(oid, []),
                    total=float(r.get(amount_field) or 0),
                    currency="USD",
                )
            )
        return result

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 20) -> list[Order]:
        return await self._fetch_recent_orders(limit)

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        orders = await self._fetch_recent_orders(50)
        return next((o for o in orders if o.order_id == order_id), None)

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return UserPreferences(user_id=session.user_id, display_name="Guest")

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        return []

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        return Cart()

    async def remove_from_cart(
        self, session: ShoppingSessionContext, product_id: str
    ) -> Cart:
        return Cart()

    # ------------------------------------------------------------------
    # Optional: merchant context sent on every turn
    # ------------------------------------------------------------------

    async def get_merchant_context(
        self, session: MerchantSessionContext
    ) -> dict[str, Any] | None:
        return {
            "store": STORE_NAME,
            "data_anchor": _DEFAULT_SINCE[:10],
            "note": (
                "Analytics agent for ETS. Ask about profiles created by the Platform team "
                "or order revenue since Aug 18 2026."
            ),
        }
