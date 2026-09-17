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
    PendingQuoteApproval,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)
from shopping_agent import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStep,
    Asset,
    AssetStatus,
    Cart,
    NotOffered,
    Order,
    OrderItem,
    OrderStatus,
    Policy,
    Product,
    ProductDetails,
    Promotion,
    Quote,
    QuoteItem,
    QuoteStatus,
    SearchFilters,
    ShoppingSessionContext,
    Subscription,
    SubscriptionStatus,
    Unavailable,
    UserPreferences,
)

DATA_DIR = Path(__file__).parent.parent  # examples/salesforce/
STORE_NAME = "Salesforce"

PRODUCT_IMAGES_DIR = DATA_DIR / "storefront-web" / "public" / "products"

# Absolute base for image URLs. Railway sets RAILWAY_PUBLIC_DOMAIN automatically;
# IMAGE_BASE_URL can override. Empty string = relative paths (local dev only).
_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
_IMAGE_BASE = (
    os.environ.get("IMAGE_BASE_URL")
    or (f"https://{_railway_domain}" if _railway_domain else "")
)

# Maps ProductCode → image path served from /products/
_PRODUCT_IMAGES: dict[str, str] = {
    "ITH-LPT-001": f"{_IMAGE_BASE}/products/ITH-laptop.jpg",
    "ITH-LPT-002": f"{_IMAGE_BASE}/products/ITH-laptop.jpg",
    "ITH-LPT-003": f"{_IMAGE_BASE}/products/ITH-workstation.jpg",
    "ITH-LPT-004": f"{_IMAGE_BASE}/products/ITH-laptop.jpg",
    "ITH-SRV-001": f"{_IMAGE_BASE}/products/ITH-server.jpg",
    "ITH-SRV-002": f"{_IMAGE_BASE}/products/ITH-server.jpg",
    "ITH-SRV-003": f"{_IMAGE_BASE}/products/ITH-server.jpg",
    "ITH-MON-001": f"{_IMAGE_BASE}/products/ITH-monitor.jpg",
    "ITH-MON-002": f"{_IMAGE_BASE}/products/ITH-monitor.jpg",
    "ITH-MON-003": f"{_IMAGE_BASE}/products/ITH-monitor.jpg",
    "ITH-NET-001": f"{_IMAGE_BASE}/products/ITH-switch.jpg",
    "ITH-NET-002": f"{_IMAGE_BASE}/products/ITH-switch.jpg",
    "ITH-NET-003": f"{_IMAGE_BASE}/products/ITH-firewall.jpg",
    "ITH-NET-004": f"{_IMAGE_BASE}/products/ITH-wifi.jpg",
    "ITH-ACC-001": f"{_IMAGE_BASE}/products/ITH-keyboard.jpg",
    "ITH-ACC-002": f"{_IMAGE_BASE}/products/ITH-mouse.jpg",
    "ITH-ACC-003": f"{_IMAGE_BASE}/products/ITH-dock.jpg",
    "ITH-ACC-004": f"{_IMAGE_BASE}/products/ITH-webcam.jpg",
    "ITH-ACC-005": f"{_IMAGE_BASE}/products/ITH-ups.jpg",
    "ITH-ACC-006": f"{_IMAGE_BASE}/products/ITH-kvm.jpg",
    "ITH-WS-DEV-I7": f"{_IMAGE_BASE}/products/ITH-workstation.jpg",
    "ITH-WS-DEV-I9": f"{_IMAGE_BASE}/products/ITH-workstation.jpg",
    "ITH-WS-DEV-XEO": f"{_IMAGE_BASE}/products/ITH-workstation.jpg",
}

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

# Synonym expansion for product search — maps buyer terms to catalog terms.
# Handles plurals, common abbreviations, and category synonyms.
_SEARCH_SYNONYMS: dict[str, list[str]] = {
    "computer": ["laptop", "workstation", "desktop"],
    "computers": ["laptop", "workstation", "desktop"],
    "pc": ["laptop", "workstation"],
    "pcs": ["laptop", "workstation"],
    "notebook": ["laptop", "probook"],
    "notebooks": ["laptop", "probook"],
    "monitors": ["monitor", "display"],
    "monitor": ["monitor", "display"],
    "displays": ["display", "monitor"],
    "screen": ["monitor", "display"],
    "screens": ["monitor", "display"],
    "keyboard": ["keyboard"],
    "keyboards": ["keyboard"],
    "mouse": ["mouse", "ergonomic"],
    "mice": ["mouse", "ergonomic"],
    "dock": ["docking"],
    "docks": ["docking"],
    "docking station": ["docking"],
    "hub": ["docking", "hub"],
    "server": ["server", "poweredge"],
    "servers": ["server", "poweredge"],
    "switch": ["switch"],
    "switches": ["switch"],
    "router": ["firewall", "switch"],
    "routers": ["firewall", "switch"],
    "firewall": ["firewall"],
    "wifi": ["wifi", "access point", "wireless"],
    "wireless": ["wifi", "wireless", "access point"],
    "access point": ["access point", "wifi"],
    "ups": ["ups"],
    "battery backup": ["ups"],
    "storage": ["ssd", "nvme", "storage"],
    "ssd": ["ssd", "nvme"],
    "drive": ["ssd", "nvme"],
    "drives": ["ssd", "nvme"],
    "webcam": ["webcam", "camera"],
    "camera": ["webcam", "camera"],
    "kvm": ["kvm"],
    "laptop": ["laptop", "probook"],
    "laptops": ["laptop", "probook"],
    "workstation": ["workstation", "probook", "prostation"],
    "workstations": ["workstation", "probook", "prostation"],
    "prostation": ["prostation"],
    "dev workstation": ["prostation", "workstation"],
    "developer workstation": ["prostation", "workstation"],
    "ml workstation": ["prostation", "workstation"],
    "ai workstation": ["prostation", "workstation", "xeon"],
    "i7": ["i7", "core i7"],
    "i9": ["i9", "core i9"],
    "xeon": ["xeon"],
    "configurable": ["prostation", "workstation", "laptop", "probook"],
    "configurable laptop": ["prostation", "workstation", "laptop", "probook"],
    "configurable laptops": ["prostation", "workstation", "laptop", "probook"],
    "configurable computer": ["prostation", "workstation", "laptop"],
    "configurable computers": ["prostation", "workstation", "laptop"],
    "customizable": ["prostation", "workstation", "laptop", "probook"],
    "variants": ["prostation", "workstation", "laptop"],
    "options": ["prostation", "workstation"],
    "configure": ["prostation", "workstation", "laptop"],
}

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
# Type-mapping helpers for new capabilities
# ---------------------------------------------------------------------------

def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_to_quote(row: dict[str, Any]) -> Quote:
    status_map = {
        "Draft": QuoteStatus.DRAFT,
        "Needs Review": QuoteStatus.SUBMITTED,
        "In Review": QuoteStatus.SUBMITTED,
        "Approved": QuoteStatus.APPROVED,
        "Rejected": QuoteStatus.REJECTED,
        "Presented": QuoteStatus.SUBMITTED,
        "Accepted": QuoteStatus.APPROVED,
        "Denied": QuoteStatus.REJECTED,
        "Ordered": QuoteStatus.ORDERED,
    }
    return Quote(
        quote_id=row["Id"],
        name=row.get("Name"),
        status=status_map.get(row.get("Status", ""), QuoteStatus.DRAFT),
        subtotal=float(row.get("Subtotal") or 0),
        expiry_date=_parse_dt(row.get("ExpirationDate")),
        created_at=_parse_dt(row.get("CreatedDate")) or datetime.now(UTC),
        notes=row.get("Description"),
    )


def _map_approval_status(sf_status: str) -> ApprovalStatus:
    return {
        "Pending": ApprovalStatus.PENDING,
        "Approved": ApprovalStatus.APPROVED,
        "Rejected": ApprovalStatus.REJECTED,
        "Recalled": ApprovalStatus.RECALLED,
        "Removed": ApprovalStatus.RECALLED,
    }.get(sf_status, ApprovalStatus.PENDING)


def _row_to_asset(row: dict[str, Any]) -> Asset:
    status_map = {
        "Purchased": AssetStatus.ACTIVE,
        "Shipped": AssetStatus.ACTIVE,
        "Installed": AssetStatus.ACTIVE,
        "Registered": AssetStatus.ACTIVE,
        "Obsolete": AssetStatus.RETIRED,
    }
    return Asset(
        asset_id=row["Id"],
        name=row.get("Name", "Unknown"),
        product_id=row.get("Product2Id"),
        serial_number=row.get("SerialNumber"),
        status=status_map.get(row.get("Status", ""), AssetStatus.ACTIVE),
        purchase_date=_parse_dt(row.get("InstallDate")),
        warranty_expiry=_parse_dt(row.get("UsageEndDate")),
        assigned_to=(row.get("Contact") or {}).get("Name"),
    )


def _asset_status_to_sf(status: str) -> str:
    return {
        "active": "Installed",
        "inactive": "Purchased",
        "retired": "Obsolete",
        "in_service": "Registered",
    }.get(status, "Installed")


def _row_to_promotion(row: dict[str, Any]) -> Promotion:
    return Promotion(
        promotion_id=row["Id"],
        name=row.get("Name", "Promotion"),
        description=row.get("Description") or row.get("Name", ""),
        discount_type="percentage",
        discount_value=0,
        expiry_date=_parse_dt(row.get("EndDate")),
        auto_applied=True,
    )


def _row_to_subscription(row: dict[str, Any], today: Any) -> Subscription:
    end = _parse_dt(row.get("EndDate"))
    start = _parse_dt(row.get("StartDate"))
    status = SubscriptionStatus.ACTIVE
    if end:
        delta = (end.date() - today).days
        if delta < 0:
            status = SubscriptionStatus.EXPIRED
        elif delta <= 90:
            status = SubscriptionStatus.EXPIRING_SOON
    sf_status = row.get("Status", "")
    if sf_status == "Cancelled":
        status = SubscriptionStatus.CANCELLED
    return Subscription(
        subscription_id=row["Id"],
        name=row.get("Name", row.get("ContractNumber", "Service Contract")),
        status=status,
        start_date=start or datetime.now(UTC),
        end_date=end or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Variant grouping — turns " - Suffix" siblings into a family + variants
# ---------------------------------------------------------------------------

def _group_variants(flat: dict[str, ProductDetails]) -> dict[str, ProductDetails]:
    """Group products that share a common name prefix (before ' - ') and the same
    category into a family product with ``options`` + individual variants.
    The family gets a synthetic id ``FAMILY-{prefix}`` and appears in searches;
    each variant gets ``variant_of`` and ``option_values`` set."""
    from collections import defaultdict

    groups: dict[str, list[ProductDetails]] = defaultdict(list)
    for p in flat.values():
        if " - " in p.title:
            prefix, _suffix = p.title.split(" - ", 1)
            groups[prefix].append(p)

    result: dict[str, ProductDetails] = {}
    grouped_ids: set[str] = set()

    for prefix, members in groups.items():
        if len(members) < 2:
            continue
        grouped_ids.update(m.product_id for m in members)
        members_sorted = sorted(members, key=lambda m: m.price)
        option_values = [m.title.split(" - ", 1)[1] for m in members_sorted]
        family_id = f"FAMILY-{prefix.replace(' ', '-')}"
        family = ProductDetails(
            product_id=family_id,
            title=prefix,
            price=members_sorted[0].price,
            currency=members_sorted[0].currency,
            category=members_sorted[0].category,
            short_description=members_sorted[0].short_description,
            long_description=members_sorted[0].long_description,
            in_stock=any(m.in_stock for m in members_sorted),
            image_url=members_sorted[0].image_url,
            options={"Configuration": option_values},
            variants=[
                Product(
                    product_id=m.product_id,
                    title=m.title,
                    price=m.price,
                    currency=m.currency,
                    category=m.category,
                    short_description=m.short_description,
                    image_url=m.image_url,
                    in_stock=m.in_stock,
                    option_values={"Configuration": m.title.split(" - ", 1)[1]},
                    variant_of=family_id,
                )
                for m in members_sorted
            ],
        )
        result[family_id] = family
        for m in members_sorted:
            result[m.product_id] = ProductDetails(
                **{**m.model_dump(), "variant_of": family_id,
                   "option_values": {"Configuration": m.title.split(" - ", 1)[1]}}
            )

    for pid, p in flat.items():
        if pid not in grouped_ids:
            result[pid] = p
    return result


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
        self._active_cart_id: str = ""  # cartId from last get_cart
        self._cart_item_ids: dict[str, str] = {}  # product_id → cartItemId

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
            log.debug("_account_id_for_user: skipping non-SF user_id=%r", sf_user_id)
            return None
        if sf_user_id in self._account_cache:
            return self._account_cache[sf_user_id]
        rows = await self._soql(
            f"SELECT AccountId, ContactId FROM User WHERE Id = '{sf_user_id}' LIMIT 1"
        )
        log.info("_account_id_for_user: user_id=%s rows=%s", sf_user_id, rows)
        if not rows:
            return None
        # Direct AccountId (set for community/portal users)
        account_id = rows[0].get("AccountId")
        # Fallback: resolve via Contact for internal users linked to a Contact
        if not account_id:
            contact_id = rows[0].get("ContactId")
            if contact_id:
                c_rows = await self._soql(
                    f"SELECT AccountId FROM Contact WHERE Id = '{contact_id}' LIMIT 1"
                )
                log.info("_account_id_for_user: contact lookup contact_id=%s rows=%s", contact_id, c_rows)
                account_id = (c_rows[0].get("AccountId") if c_rows else None)
        # Fallback: look up Contact by User's email (covers internal/admin users
        # who cannot have ContactId set but have a matching Contact record)
        if not account_id:
            email_rows = await self._soql(
                f"SELECT Email FROM User WHERE Id = '{sf_user_id}' LIMIT 1"
            )
            email = email_rows[0].get("Email") if email_rows else None
            if email:
                safe_email = email.replace("'", "\\'")
                c_rows = await self._soql(
                    f"SELECT AccountId FROM Contact WHERE Email = '{safe_email}' LIMIT 1"
                )
                log.info("_account_id_for_user: email fallback email=%s rows=%s", email, c_rows)
                account_id = (c_rows[0].get("AccountId") if c_rows else None)
        if not account_id:
            log.warning("_account_id_for_user: no AccountId for user_id=%s", sf_user_id)
            return None
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

    async def _load_products_from_b2b(self, webstore_id: str) -> tuple[dict, dict]:
        """Load products via B2B Commerce Products API — returns only catalog-visible products."""
        cache: dict[str, Listing] = {}
        code_to_id: dict[str, str] = {}
        page_token: str | None = None
        while True:
            params: dict = {"pageSize": 100}
            if page_token:
                params["pageParam"] = page_token
            try:
                data = await self._b2b_request(
                    "GET", f"/commerce/webstores/{webstore_id}/products", params=params
                )
            except Exception as exc:
                log.warning("B2B Products API failed: %s — falling back to PricebookEntry", exc)
                return {}, {}
            for item in data.get("products", []):
                pid = item.get("id") or ""
                if not pid or pid in cache:
                    continue
                name = item.get("name") or pid
                code = item.get("productCode") or ""
                desc = item.get("description") or None
                family = item.get("productClass") or None
                # Price comes from the entitlement's defaultPricebook
                price_info = item.get("prices") or {}
                price = float(price_info.get("listPrice") or price_info.get("unitPrice") or 0)
                cache[pid] = Listing(
                    listing_id=pid,
                    title=name,
                    price=price,
                    currency=price_info.get("currencyIsoCode") or "USD",
                    stock=0,
                    category=family,
                    status="active",
                    short_description=desc,
                    content_quality="good" if desc else "needs_work",
                )
                if code:
                    code_to_id[code] = pid
            next_page = data.get("nextPageUrl") or data.get("nextPageToken")
            if not next_page or not cache:
                break
            page_token = next_page
        log.info("B2B Products API loaded %d products", len(cache))
        return cache, code_to_id

    async def _gap_fill_recent_products(
        self, cache: dict, code_to_id: dict, window_days: int = 30
    ) -> None:
        """Add entitled products created recently that the B2B index may not have indexed yet.

        Uses SOQL subqueries so no product IDs are loaded into application memory and
        there is no IN-list whose size grows with catalog size. Only products created
        within ``window_days`` are checked — older ones will already be in the B2B index.
        """
        try:
            records = await self._soql(
                f"SELECT Product2Id, Product2.Name, Product2.Description, "
                f"Product2.Family, Product2.ProductCode, UnitPrice "
                f"FROM PricebookEntry "
                f"WHERE IsActive = true AND UnitPrice > 0 AND Product2.IsActive = true "
                f"AND Product2Id IN (SELECT ProductId FROM ProductCategoryProduct) "
                f"AND Product2Id IN (SELECT ProductId FROM CommerceEntitlementProduct) "
                f"AND Product2.CreatedDate > LAST_N_DAYS:{window_days} "
                f"ORDER BY UnitPrice DESC "
                f"LIMIT 200"
            )
        except Exception as exc:
            log.warning("Gap-fill query failed: %s", exc)
            return
        added = 0
        for r in records:
            pid = r.get("Product2Id", "")
            if not pid or pid in cache:
                continue
            p2 = r.get("Product2") or {}
            code = p2.get("ProductCode") or ""
            cache[pid] = Listing(
                listing_id=pid,
                title=p2.get("Name") or pid,
                price=float(r.get("UnitPrice") or 0),
                currency="USD",
                stock=0,
                category=p2.get("Family") or None,
                status="active",
                short_description=p2.get("Description") or None,
                content_quality="good" if p2.get("Description") else "needs_work",
                image_url=_PRODUCT_IMAGES.get(code),
            )
            if code:
                code_to_id[code] = pid
            added += 1
        if added:
            log.info("Gap-fill: added %d recently created products not yet in B2B index", added)

    async def _ensure_products_loaded(self) -> None:
        if self._products_loaded:
            return
        # Primary: B2B Products API (Commerce-native pricing, stock, entitlement).
        # Gap-fill: products created recently may not be in the search index yet — a
        # targeted SOQL subquery finds them without loading any IDs into app memory.
        # Full PricebookEntry fallback when the B2B API is unavailable.
        try:
            webstore_id = await self._ensure_webstore_id()
            cache, code_to_id = await self._load_products_from_b2b(webstore_id)
        except Exception:
            cache, code_to_id = {}, {}

        if cache:
            await self._gap_fill_recent_products(cache, code_to_id)
        else:
            log.info("B2B Products API unavailable — loading from PricebookEntry")
            try:
                records = await self._soql(
                    "SELECT Product2Id, Product2.Name, Product2.Description, "
                    "Product2.Family, Product2.ProductCode, UnitPrice "
                    "FROM PricebookEntry "
                    "WHERE IsActive = true AND Product2.IsActive = true "
                    "AND Product2Id IN (SELECT ProductId FROM ProductCategoryProduct) "
                    "AND Product2Id IN (SELECT ProductId FROM CommerceEntitlementProduct) "
                    "ORDER BY Product2.Name LIMIT 2000"
                )
            except Exception as exc:
                log.error("PricebookEntry fallback failed: %s", exc)
                records = []
            cache, code_to_id = {}, {}
            for r in records:
                pid = r.get("Product2Id", "")
                if not pid or pid in cache:
                    continue
                p2 = r.get("Product2") or {}
                code = p2.get("ProductCode") or ""
                cache[pid] = Listing(
                    listing_id=pid,
                    title=p2.get("Name") or pid,
                    price=float(r.get("UnitPrice") or 0),
                    currency="USD",
                    stock=0,
                    category=p2.get("Family") or None,
                    status="active",
                    short_description=p2.get("Description") or None,
                    content_quality="good" if p2.get("Description") else "needs_work",
                    image_url=_PRODUCT_IMAGES.get(code),
                )
                if code:
                    code_to_id[code] = pid
        log.info("Product catalog ready: %d products", len(cache))
        self._products_cache = cache
        self._code_to_id = code_to_id
        # Build ProductDetails, grouping variant products into families.
        flat = {pid: self._listing_to_product_details(listing) for pid, listing in cache.items()}
        self.products = _group_variants(flat)
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
            image_url=listing.image_url,
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
        # Variants are accessed through their family; exclude them from search results.
        results = [p for p in self.products.values() if not p.variant_of]
        if query:
            q = query.lower().strip()
            if q in _SEARCH_SYNONYMS:
                terms: list[str] = _SEARCH_SYNONYMS[q]
            else:
                expanded: set[str] = set()
                for word in q.split():
                    syns = _SEARCH_SYNONYMS.get(word)
                    if syns:
                        expanded.update(syns)
                    else:
                        expanded.add(word)
                terms = list(expanded)

            def _matches(p: ProductDetails) -> bool:
                text = " ".join(filter(None, [
                    p.title, p.brand, p.category,
                    p.short_description, p.long_description,
                ])).lower()
                return any(t in text for t in terms)

            results = [p for p in results if _matches(p)]
        if filters:
            if filters.category:
                cat = filters.category.lower()
                results = [p for p in results if p.category and cat in p.category.lower()]
            if filters.brand:
                brand = filters.brand.lower()
                results = [p for p in results if p.brand and brand in p.brand.lower()]
            if filters.min_price is not None:
                results = [p for p in results if p.price >= filters.min_price]
            if filters.max_price is not None:
                results = [p for p in results if p.price <= filters.max_price]
            if filters.min_rating is not None:
                results = [p for p in results if p.rating is not None and p.rating >= filters.min_rating]
            if filters.in_stock_only:
                results = [p for p in results if p.in_stock]
            if filters.attributes:
                for attr_key, attr_val in filters.attributes.items():
                    ak, av = attr_key.lower(), attr_val.lower()
                    results = [
                        p for p in results
                        if any(
                            ak in k.lower() and av in v.lower()
                            for k, v in p.attributes.items()
                        ) or av in (p.short_description or "").lower()
                    ]
            if filters.sort == "price_asc":
                results = sorted(results, key=lambda p: p.price)
            elif filters.sort == "price_desc":
                results = sorted(results, key=lambda p: p.price, reverse=True)
            elif filters.sort == "rating":
                results = sorted(results, key=lambda p: p.rating or 0, reverse=True)
        return results[:limit]

    async def get_product_categories(self, session: ShoppingSessionContext) -> list[str]:
        await self._ensure_products_loaded()
        cats = sorted({p.category for p in self.products.values() if p.category})
        return cats

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

    # -- Quote approvals -----------------------------------------------------------

    async def get_pending_quote_approvals(
        self, session: MerchantSessionContext
    ) -> list[PendingQuoteApproval]:
        rows = await self._soql(
            "SELECT Id, ProcessInstance.TargetObjectId, "
            "ProcessInstance.Status, ProcessInstance.CreatedDate, "
            "ProcessInstance.CreatedBy.Name, "
            "ProcessInstance.TargetObject.Name "
            "FROM ProcessInstanceWorkitem "
            "WHERE ProcessInstance.Status = 'Pending' "
            "AND ProcessInstance.TargetObject.Type = 'Quote' "
            "ORDER BY ProcessInstance.CreatedDate ASC"
        )
        if not rows:
            return []

        quote_ids = list({r.get("ProcessInstance", {}).get("TargetObjectId", "") for r in rows if r.get("ProcessInstance", {}).get("TargetObjectId")})
        quote_map: dict[str, dict] = {}
        if quote_ids:
            in_clause = ", ".join(f"'{qid}'" for qid in quote_ids)
            q_rows = await self._soql(
                f"SELECT Id, Name, GrandTotal, Opportunity.Account.Name "
                f"FROM Quote WHERE Id IN ({in_clause})"
            )
            for q in q_rows:
                quote_map[q["Id"]] = q

        results = []
        for row in rows:
            pi = row.get("ProcessInstance") or {}
            quote_id = pi.get("TargetObjectId", "")
            quote = quote_map.get(quote_id, {})
            opp = (quote.get("Opportunity") or {})
            account_name = (opp.get("Account") or {}).get("Name")
            results.append(PendingQuoteApproval(
                workitem_id=row["Id"],
                quote_id=quote_id,
                quote_name=quote.get("Name") or pi.get("TargetObject", {}).get("Name", quote_id),
                account_name=account_name,
                grand_total=float(quote.get("GrandTotal") or 0),
                submitted_by=(pi.get("CreatedBy") or {}).get("Name"),
                submitted_at=_parse_dt(pi.get("CreatedDate")),
            ))
        return results

    async def approve_quote(
        self, session: MerchantSessionContext, workitem_id: str, comments: str = ""
    ) -> dict:
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/process/approvals/",
                headers={**headers, "Content-Type": "application/json"},
                json={"requests": [{
                    "actionType": "Approve",
                    "contextId": workitem_id,
                    "comments": comments or "Approved via merchant agent",
                }]},
            )
            resp.raise_for_status()
            result = resp.json()
        if result and not result[0].get("success", True):
            errors = result[0].get("errors", [])
            raise RuntimeError(f"Approval failed: {errors}")
        quote_id = result[0].get("entityId", "") if result else ""
        return {"success": True, "quote_id": quote_id, "workitem_id": workitem_id}

    async def reject_quote(
        self, session: MerchantSessionContext, workitem_id: str, comments: str = ""
    ) -> dict:
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/process/approvals/",
                headers={**headers, "Content-Type": "application/json"},
                json={"requests": [{
                    "actionType": "Reject",
                    "contextId": workitem_id,
                    "comments": comments or "Rejected via merchant agent",
                }]},
            )
            resp.raise_for_status()
            result = resp.json()
        if result and not result[0].get("success", True):
            errors = result[0].get("errors", [])
            raise RuntimeError(f"Rejection failed: {errors}")
        quote_id = result[0].get("entityId", "") if result else ""
        return {"success": True, "quote_id": quote_id, "workitem_id": workitem_id}

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

    async def checkout_handoff(self, session: ShoppingSessionContext, cart: Cart) -> list:
        """Return link to native B2B Commerce checkout so user can complete purchase there."""
        from shopping_agent import CheckoutHandoff
        community_url = os.environ.get("SF_COMMUNITY_URL", "").rstrip("/")
        if not community_url:
            return []
        return [CheckoutHandoff(label="Proceed to checkout", url=f"{community_url}/cart")]

    async def _get_buyer_cart_id(self, sf_user_id: str, webstore_id: str) -> tuple[str, str]:
        """Return (cartId, currencyIsoCode) for the buyer's active WebCart via SOQL.

        Reading via SOQL on WebCart.OwnerId guarantees we see the same cart the native
        B2B Commerce storefront shows.  Returns ("", "USD") when no active cart exists.
        """
        rows = await self._soql(
            f"SELECT Id, CurrencyIsoCode FROM WebCart "
            f"WHERE OwnerId = '{sf_user_id}' AND Status = 'Active' "
            f"AND WebStoreId = '{webstore_id}' "
            f"ORDER BY LastModifiedDate DESC LIMIT 1"
        )
        if not rows:
            return "", "USD"
        return rows[0].get("Id", ""), rows[0].get("CurrencyIsoCode", "USD")

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        sf_user_id = session.user_id
        if not sf_user_id or not sf_user_id.startswith("005"):
            return Cart()
        webstore_id = await self._ensure_webstore_id()

        # Read via SOQL so we see the same cart as the native B2B storefront
        cart_id, currency = await self._get_buyer_cart_id(sf_user_id, webstore_id)
        log.debug("get_cart: cartId=%s currency=%s user=%s", cart_id, currency, sf_user_id)
        if not cart_id:
            return Cart()
        self._active_cart_id = cart_id

        # Use B2B Commerce REST API for cart items — the integration user has B2B API access
        # but lacks SOQL read on WebCartItem; the API call handles buyer context via effectiveAccountId
        account_id = await self._account_id_for_user(sf_user_id)
        params: dict = {}
        if account_id:
            params["effectiveAccountId"] = account_id
        try:
            data = await self._b2b_request(
                "GET",
                f"/commerce/webstores/{webstore_id}/carts/{cart_id}/cart-items",
                params=params,
            )
            cart, item_ids = self._parse_b2b_cart_items(data, currency)
            self._cart_item_ids = item_ids
            log.debug("get_cart: %d items from B2B cart-items API", len(cart.items))
        except Exception as exc:
            log.warning("get_cart: cart-items API failed, returning header-only cart: %s", exc)
            cart = Cart(currency=currency)
        return cart

    async def _add_to_cart_direct(
        self, cart_id: str, product_id: str, quantity: int
    ) -> None:
        """Fallback for products not yet in the B2B search index.

        The Commerce cart-items API validates against the search index and returns
        NOT_FOUND for recently created products. Direct CartItem SObject insert
        bypasses that check while still setting correct pricing from PricebookEntry.
        """
        # Get CartDeliveryGroup (required field on CartItem)
        cdg_rows = await self._soql(
            f"SELECT Id FROM CartDeliveryGroup WHERE CartId = '{cart_id}' LIMIT 1"
        )
        if not cdg_rows:
            raise ValueError("No CartDeliveryGroup found for cart — cannot add item directly")
        cdg_id = cdg_rows[0]["Id"]

        # Use cached product price if already loaded; otherwise query the highest non-zero
        # PricebookEntry price to avoid picking up the Standard Price Book's $0 entry.
        cached = self.products.get(product_id)
        if cached and cached.price > 0:
            price = cached.price
            name = cached.title
        else:
            pbe_rows = await self._soql(
                f"SELECT Product2.Name, UnitPrice FROM PricebookEntry "
                f"WHERE Product2Id = '{product_id}' AND IsActive = true AND UnitPrice > 0 "
                f"ORDER BY UnitPrice DESC LIMIT 1"
            )
            if not pbe_rows:
                raise ValueError(f"No active PricebookEntry for product {product_id}")
            price = float(pbe_rows[0].get("UnitPrice") or 0)
            name = (pbe_rows[0].get("Product2") or {}).get("Name") or product_id

        await self._b2b_request(
            "POST",
            "/sobjects/CartItem",
            json={
                "CartId": cart_id,
                "CartDeliveryGroupId": cdg_id,
                "Product2Id": product_id,
                "Quantity": quantity,
                "Type": "Product",
                "Name": name,
                "SalesPrice": price,
                "ListPrice": price,
            },
        )

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        sf_user_id = session.user_id
        if not sf_user_id or not sf_user_id.startswith("005"):
            raise ValueError(
                "Cannot add to cart: no valid Salesforce user in session. "
                "Please ensure you are logged in as a B2B Commerce community user."
            )
        account_id = await self._account_id_for_user(sf_user_id)
        if not account_id:
            raise ValueError(
                "Cannot add to cart: no buyer account found for this user. "
                "Please ensure you are logged in as a B2B Commerce community user."
            )
        webstore_id = await self._ensure_webstore_id()
        cart_id, _ = await self._get_buyer_cart_id(sf_user_id, webstore_id)

        endpoint = (
            f"/commerce/webstores/{webstore_id}/carts/{cart_id}/cart-items"
            if cart_id
            else f"/commerce/webstores/{webstore_id}/carts/active/cart-items"
        )
        try:
            await self._b2b_request(
                "POST", endpoint,
                params={"effectiveAccountId": account_id},
                json={"productId": product_id, "quantity": str(quantity), "type": "Product"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and cart_id:
                # Product not in B2B search index yet — insert CartItem directly.
                log.info(
                    "add_to_cart: product %s not in B2B index, using direct CartItem insert",
                    product_id,
                )
                await self._add_to_cart_direct(cart_id, product_id, quantity)
            elif exc.response.status_code == 404 and not cart_id:
                raise Unavailable(
                    "This product is not yet available in the store catalog. "
                    "Please try again shortly or contact your sales representative."
                )
            else:
                raise
        return await self.get_cart(session)

    def _parse_b2b_cart(self, data: dict[str, Any]) -> tuple[Cart, dict[str, str]]:
        """Parse cart from GET /carts/active?fieldsToExpand=cartItems response.

        Returns (Cart, {product_id: cartItemId}) — flat records when expanded inline.
        """
        from shopping_agent import CartItem
        items = []
        item_ids: dict[str, str] = {}
        cart_items_block = data.get("cartItems") or {}
        records = cart_items_block.get("records", []) if isinstance(cart_items_block, dict) else []
        for entry in records:
            # Inline expansion: fields are flat; nested format has a 'cartItem' sub-key
            row = entry.get("cartItem") if "cartItem" in entry else entry
            product_id = row.get("productId", "")
            cart_item_id = row.get("cartItemId", "")
            name = row.get("name", product_id)
            price = float(next(
                (v for v in [
                    row.get("unitAdjustedPrice"),
                    row.get("salesPrice"),
                    row.get("listPrice"),
                ] if v is not None),
                0,
            ))
            qty = int(float(row.get("quantity") or 1))
            image_url = self._products_cache.get(product_id, None)
            image_url = image_url.image_url if image_url else None
            items.append(CartItem(product_id=product_id, title=name, price=price, quantity=qty, image_url=image_url))
            if product_id and cart_item_id:
                item_ids[product_id] = cart_item_id
        currency = data.get("currencyIsoCode", "USD")
        return Cart(items=items, currency=currency), item_ids

    def _parse_b2b_cart_items(self, data: dict[str, Any], currency: str = "USD") -> tuple[Cart, dict[str, str]]:
        """Parse cart from GET /carts/{cartId}/cart-items response.

        Returns (Cart, {product_id: cartItemId}) — each record nested under 'cartItem'.
        """
        from shopping_agent import CartItem
        items = []
        item_ids: dict[str, str] = {}
        for entry in data.get("cartItems", []):
            row = entry.get("cartItem", entry)
            product_id = row.get("productId", "")
            cart_item_id = row.get("cartItemId", "")
            name = row.get("name", product_id)
            price = float(next(
                (v for v in [
                    row.get("unitAdjustedPrice"),
                    row.get("salesPrice"),
                    row.get("listPrice"),
                ] if v is not None),
                0,
            ))
            qty = int(float(row.get("quantity") or 1))
            image_url = self._products_cache.get(product_id, None)
            image_url = image_url.image_url if image_url else None
            items.append(CartItem(product_id=product_id, title=name, price=price, quantity=qty, image_url=image_url))
            if product_id and cart_item_id:
                item_ids[product_id] = cart_item_id
        return Cart(items=items, currency=currency), item_ids

    # ------------------------------------------------------------------
    # Storefront orders — fetched live from OrderSummary + OrderItemSummary
    # ------------------------------------------------------------------

    async def _fetch_recent_orders(self, limit: int = 20) -> list[Order]:
        try:
            return await self._fetch_recent_orders_inner(limit)
        except Exception as exc:
            log.warning("_fetch_recent_orders failed: %s", exc)
            return []

    async def _fetch_recent_orders_inner(self, limit: int = 20) -> list[Order]:
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
                        (r.get("CreatedDate") or "2026-01-01T00:00:00.000+00:00")
                        .replace("Z", "+00:00")
                        .replace("+0000", "+00:00")
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
        orders = await self._fetch_recent_orders(20)
        return next((o for o in orders if o.order_id == order_id), None)

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return UserPreferences(user_id=session.user_id, display_name="Guest")

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        return []

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            return Cart()
        # Refresh to get current cart item IDs
        await self.get_cart(session)
        cart_item_id = self._cart_item_ids.get(product_id)
        if not cart_item_id or not self._active_cart_id:
            return await self.get_cart(session)
        webstore_id = await self._ensure_webstore_id()
        if quantity <= 0:
            await self._b2b_request(
                "DELETE",
                f"/commerce/webstores/{webstore_id}/carts/{self._active_cart_id}/cart-items/{cart_item_id}",
                params={"effectiveAccountId": account_id},
            )
        else:
            await self._b2b_request(
                "PATCH",
                f"/commerce/webstores/{webstore_id}/carts/{self._active_cart_id}/cart-items/{cart_item_id}",
                params={"effectiveAccountId": account_id},
                json={"quantity": str(quantity)},
            )
        return await self.get_cart(session)

    async def remove_from_cart(
        self, session: ShoppingSessionContext, product_id: str
    ) -> Cart:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            return Cart()
        # Refresh to get current cart item IDs
        cart = await self.get_cart(session)
        cart_item_id = self._cart_item_ids.get(product_id)
        if not cart_item_id or not self._active_cart_id:
            # product not found in cart — return current state
            return cart
        webstore_id = await self._ensure_webstore_id()
        await self._b2b_request(
            "DELETE",
            f"/commerce/webstores/{webstore_id}/carts/{self._active_cart_id}/cart-items/{cart_item_id}",
            params={"effectiveAccountId": account_id},
        )
        return await self.get_cart(session)

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

    # ------------------------------------------------------------------
    # Quotes — Salesforce Quote object
    # ------------------------------------------------------------------

    async def get_quotes(self, session: ShoppingSessionContext) -> list[Quote]:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            raise NotOffered
        rows = await self._soql(
            f"SELECT Id, Name, Status, Subtotal, ExpirationDate, CreatedDate, Description "
            f"FROM Quote WHERE AccountId = '{account_id}' "
            f"ORDER BY CreatedDate DESC LIMIT 10"
        )
        return [_row_to_quote(r) for r in rows]

    async def get_quote(self, session: ShoppingSessionContext, quote_id: str) -> Quote | None:
        rows = await self._soql(
            f"SELECT Id, Name, Status, Subtotal, ExpirationDate, CreatedDate, Description "
            f"FROM Quote WHERE Id = '{quote_id}' LIMIT 1"
        )
        if not rows:
            return None
        quote = _row_to_quote(rows[0])
        # fetch line items
        lines = await self._soql(
            f"SELECT Id, Product2Id, Product2.Name, Quantity, UnitPrice, TotalPrice "
            f"FROM QuoteLineItem WHERE QuoteId = '{quote_id}'"
        )
        quote.items = [
            QuoteItem(
                product_id=li.get("Product2Id", ""),
                line_item_id=li.get("Id"),
                title=(li.get("Product2") or {}).get("Name", "Unknown"),
                quantity=int(li.get("Quantity") or 1),
                unit_price=float(li.get("UnitPrice") or 0),
            )
            for li in lines
        ]
        return quote

    async def _standard_pricebook_id(self) -> str | None:
        """Return the Standard Pricebook2 Id (cached after first lookup)."""
        if hasattr(self, "_std_pb_id"):
            return self._std_pb_id  # type: ignore[attr-defined]
        rows = await self._soql(
            "SELECT Id FROM Pricebook2 WHERE IsStandard = true AND IsActive = true LIMIT 1"
        )
        pb_id = rows[0]["Id"] if rows else None
        self._std_pb_id = pb_id  # type: ignore[attr-defined]
        return pb_id

    async def _pricebook_entry_id(self, product2_id: str, pricebook_id: str) -> str | None:
        """Return the PricebookEntry Id for a product in the given pricebook."""
        safe_prod = product2_id.replace("'", "\\'")
        safe_pb   = pricebook_id.replace("'", "\\'")
        rows = await self._soql(
            f"SELECT Id FROM PricebookEntry "
            f"WHERE Product2Id = '{safe_prod}' AND Pricebook2Id = '{safe_pb}' "
            f"AND IsActive = true LIMIT 1"
        )
        return rows[0]["Id"] if rows else None

    async def create_quote(
        self, session: ShoppingSessionContext, name: str | None = None, notes: str | None = None
    ) -> Quote:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            raise NotOffered
        cart = await self.get_cart(session)
        if not cart.items:
            raise NotOffered
        pb_id = await self._standard_pricebook_id()
        # Salesforce FLS often prevents writing AccountId directly on Quote.
        # Link via OpportunityId instead — SF auto-populates AccountId from it.
        opp_rows = await self._soql(
            f"SELECT Id FROM Opportunity WHERE AccountId = '{account_id}' "
            f"AND IsClosed = false ORDER BY CreatedDate DESC LIMIT 1"
        )
        opp_id = opp_rows[0]["Id"] if opp_rows else None
        headers = await self._token_headers()
        payload: dict[str, Any] = {
            "Name": name or f"Quote {datetime.now(UTC).strftime('%Y-%m-%d')}",
            "Status": "Draft",
        }
        if opp_id:
            payload["OpportunityId"] = opp_id
        else:
            payload["AccountId"] = account_id
        if pb_id:
            payload["Pricebook2Id"] = pb_id
        if notes:
            payload["Description"] = notes
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/sobjects/Quote",
                headers={**headers, "Content-Type": "application/json"},
                json=payload,
            )
            if not resp.is_success:
                log.error(
                    "create_quote: Quote POST failed %s — payload=%s — response=%s",
                    resp.status_code,
                    payload,
                    resp.text,
                )
                sf_errors = resp.json() if resp.content else []
                msg = sf_errors[0].get("message", resp.text) if sf_errors else resp.text
                raise Unavailable(msg[:300])
            quote_id = resp.json()["id"]
            for item in cart.items:
                pbe_id = (
                    await self._pricebook_entry_id(item.product_id, pb_id) if pb_id else None
                )
                if not pbe_id:
                    raise Unavailable(
                        f"Product '{item.title}' is not in the active pricebook and cannot be "
                        "quoted. Remove it from the cart before creating a quote."
                    )
                line: dict[str, Any] = {
                    "QuoteId": quote_id,
                    "PricebookEntryId": pbe_id,
                    "Quantity": item.quantity,
                    "UnitPrice": item.price,
                }
                log.debug(
                    "create_quote: QuoteLineItem payload product=%s pbe=%s", item.product_id, pbe_id
                )
                resp2 = await client.post(
                    f"{self._base}/services/data/v62.0/sobjects/QuoteLineItem",
                    headers={**headers, "Content-Type": "application/json"},
                    json=line,
                )
                if not resp2.is_success:
                    raise Unavailable(f"Failed to add '{item.title}' to quote: {resp2.text[:200]}")
        quote = await self.get_quote(session, quote_id)
        return quote or Quote(
            quote_id=quote_id,
            status=QuoteStatus.DRAFT,
            subtotal=cart.subtotal,
            created_at=datetime.now(UTC),
        )

    async def submit_quote(self, session: ShoppingSessionContext, quote_id: str) -> Quote:
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{self._base}/services/data/v62.0/sobjects/Quote/{quote_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"Status": "Needs Review"},
            )
            resp.raise_for_status()
        quote = await self.get_quote(session, quote_id)
        return quote or Quote(
            quote_id=quote_id,
            status=QuoteStatus.SUBMITTED,
            subtotal=0,
            created_at=datetime.now(UTC),
        )

    async def update_quote_item(
        self, session: ShoppingSessionContext, quote_id: str, line_item_id: str, quantity: int
    ) -> Quote:
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{self._base}/services/data/v62.0/sobjects/QuoteLineItem/{line_item_id}",
                headers={**headers, "Content-Type": "application/json"},
                json={"Quantity": quantity},
            )
            if not resp.is_success:
                log.error("update_quote_item: PATCH failed %s: %s", resp.status_code, resp.text)
                resp.raise_for_status()
        quote = await self.get_quote(session, quote_id)
        return quote or Quote(
            quote_id=quote_id, status=QuoteStatus.DRAFT, subtotal=0, created_at=datetime.now(UTC)
        )

    async def add_product_to_quote(
        self, session: ShoppingSessionContext, quote_id: str, product_id: str, quantity: int
    ) -> Quote:
        pb_id = await self._standard_pricebook_id()
        pbe_id = await self._pricebook_entry_id(product_id, pb_id) if pb_id else None
        if not pbe_id:
            raise Unavailable("Product is not in the active pricebook.")
        # Get unit price from PricebookEntry
        price_rows = await self._soql(
            f"SELECT UnitPrice FROM PricebookEntry WHERE Id = '{pbe_id}' LIMIT 1"
        )
        unit_price = float(price_rows[0].get("UnitPrice", 0)) if price_rows else 0.0
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/sobjects/QuoteLineItem",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "QuoteId": quote_id,
                    "PricebookEntryId": pbe_id,
                    "Product2Id": product_id,
                    "Quantity": quantity,
                    "UnitPrice": unit_price,
                },
            )
            if not resp.is_success:
                log.error("add_product_to_quote: POST failed %s: %s", resp.status_code, resp.text)
                raise Unavailable(resp.json()[0].get("message", resp.text)[:300] if resp.content else resp.text)
        quote = await self.get_quote(session, quote_id)
        return quote or Quote(
            quote_id=quote_id, status=QuoteStatus.DRAFT, subtotal=0, created_at=datetime.now(UTC)
        )

    async def remove_quote_item(
        self, session: ShoppingSessionContext, quote_id: str, line_item_id: str
    ) -> Quote:
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{self._base}/services/data/v62.0/sobjects/QuoteLineItem/{line_item_id}",
                headers=headers,
            )
            if not resp.is_success:
                log.error("remove_quote_item: DELETE failed %s: %s", resp.status_code, resp.text)
                resp.raise_for_status()
        quote = await self.get_quote(session, quote_id)
        return quote or Quote(
            quote_id=quote_id, status=QuoteStatus.DRAFT, subtotal=0, created_at=datetime.now(UTC)
        )

    async def update_quote(
        self,
        session: ShoppingSessionContext,
        quote_id: str,
        name: str | None = None,
        notes: str | None = None,
        expiry_date: str | None = None,
    ) -> Quote:
        payload: dict[str, Any] = {}
        if name:
            payload["Name"] = name
        if notes is not None:
            payload["Description"] = notes
        if expiry_date:
            payload["ExpirationDate"] = expiry_date
        if payload:
            headers = await self._token_headers()
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{self._base}/services/data/v62.0/sobjects/Quote/{quote_id}",
                    headers={**headers, "Content-Type": "application/json"},
                    json=payload,
                )
                if not resp.is_success:
                    log.error("update_quote: PATCH failed %s: %s", resp.status_code, resp.text)
                    resp.raise_for_status()
        quote = await self.get_quote(session, quote_id)
        return quote or Quote(
            quote_id=quote_id, status=QuoteStatus.DRAFT, subtotal=0, created_at=datetime.now(UTC)
        )

    # ------------------------------------------------------------------
    # Approvals — Salesforce Process Approvals API
    # ------------------------------------------------------------------

    async def load_quote_to_cart(
        self, session: ShoppingSessionContext, quote_id: str
    ) -> Cart:
        rows = await self._soql(
            f"SELECT Id, (SELECT Product2Id, Quantity FROM QuoteLineItems) "
            f"FROM Quote WHERE Id = '{quote_id}' LIMIT 1"
        )
        if not rows:
            raise NotOffered
        lines = (rows[0].get("QuoteLineItems") or {}).get("records", [])
        if not lines:
            raise NotOffered
        cart: Cart | None = None
        for line in lines:
            product_id = line.get("Product2Id", "")
            qty = int(line.get("Quantity") or 1)
            if product_id:
                cart = await self.add_to_cart(session, product_id, qty)
        return cart or await self.get_cart(session)

    async def submit_for_approval(
        self,
        session: ShoppingSessionContext,
        subject_type: str,
        subject_id: str,
    ) -> ApprovalRequest:
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/process/approvals/",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "requests": [{
                        "actionType": "Submit",
                        "contextId": subject_id,
                        "comments": f"Submitted via shopping agent ({subject_type})",
                    }]
                },
            )
            resp.raise_for_status()
            result = resp.json()
        if result and not result[0].get("success", True):
            errors = result[0].get("errors", [])
            raise RuntimeError(f"Approval submission failed: {errors}")
        instance_id = result[0].get("instanceId", subject_id) if result else subject_id
        return ApprovalRequest(
            request_id=instance_id,
            subject_type=subject_type,  # type: ignore[arg-type]
            subject_id=subject_id,
            status=ApprovalStatus.PENDING,
            steps=[],
            submitted_at=datetime.now(UTC),
            total_amount=0,
        )

    async def get_approval_status(
        self, session: ShoppingSessionContext, request_id: str
    ) -> ApprovalRequest | None:
        rows = await self._soql(
            f"SELECT Id, Status, TargetObjectId, CreatedDate, "
            f"(SELECT Id, StepStatus, ActorId, Actor.Name, Comments, CreatedDate "
            f"FROM StepsAndWorkitems) "
            f"FROM ProcessInstance WHERE Id = '{request_id}' LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        steps = [
            ApprovalStep(
                step_number=i + 1,
                approver_name=(s.get("Actor") or {}).get("Name", "Unknown"),
                status=_map_approval_status(s.get("StepStatus", "")),
                comments=s.get("Comments"),
                acted_at=_parse_dt(s.get("CreatedDate")),
            )
            for i, s in enumerate(row.get("StepsAndWorkitems", {}).get("records", []))
        ]
        return ApprovalRequest(
            request_id=row["Id"],
            subject_type="order",
            subject_id=row.get("TargetObjectId", ""),
            status=_map_approval_status(row.get("Status", "")),
            steps=steps,
            submitted_at=_parse_dt(row.get("CreatedDate")) or datetime.now(UTC),
            total_amount=0,
        )

    async def recall_approval_request(
        self, session: ShoppingSessionContext, request_id: str
    ) -> ApprovalRequest:
        # Get the target object id first
        rows = await self._soql(
            f"SELECT TargetObjectId FROM ProcessInstance WHERE Id = '{request_id}' LIMIT 1"
        )
        target_id = rows[0]["TargetObjectId"] if rows else request_id
        headers = await self._token_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/process/approvals/",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "requests": [{
                        "actionType": "Removed",
                        "contextId": target_id,
                        "comments": "Recalled via shopping agent",
                    }]
                },
            )
            resp.raise_for_status()
        return ApprovalRequest(
            request_id=request_id,
            subject_type="order",
            subject_id=target_id,
            status=ApprovalStatus.RECALLED,
            steps=[],
            submitted_at=datetime.now(UTC),
            total_amount=0,
        )

    # ------------------------------------------------------------------
    # Assets — Salesforce Asset object (installed base)
    # ------------------------------------------------------------------

    async def get_assets(
        self,
        session: ShoppingSessionContext,
        category: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[Asset]:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            raise NotOffered
        where = f"AccountId = '{account_id}'"
        if status:
            sf_status = _asset_status_to_sf(status)
            where += f" AND Status = '{sf_status}'"
        if category:
            safe_cat = category.replace("'", "\\'")
            where += f" AND Product2.Family LIKE '%{safe_cat}%'"
        if query:
            safe = query.replace("'", "\\'")
            where += (
                f" AND (Name LIKE '%{safe}%' OR SerialNumber LIKE '%{safe}%'"
                f" OR Product2.Name LIKE '%{safe}%')"
            )
        rows = await self._soql(
            f"SELECT Id, Name, Product2Id, Product2.Name, SerialNumber, Status, "
            f"InstallDate, UsageEndDate, Quantity, ContactId, Contact.Name "
            f"FROM Asset WHERE {where} ORDER BY Name ASC LIMIT 50"
        )
        return [_row_to_asset(r) for r in rows]

    async def get_asset_details(
        self, session: ShoppingSessionContext, asset_id: str
    ) -> Asset | None:
        safe = asset_id.replace("'", "\\'")
        rows = await self._soql(
            f"SELECT Id, Name, Product2Id, Product2.Name, SerialNumber, Status, "
            f"InstallDate, UsageEndDate, Quantity, ContactId, Contact.Name "
            f"FROM Asset WHERE Id = '{safe}' OR SerialNumber = '{safe}' LIMIT 1"
        )
        return _row_to_asset(rows[0]) if rows else None

    # ------------------------------------------------------------------
    # Promotions — Salesforce Promotion object (B2B Commerce)
    # ------------------------------------------------------------------

    async def get_promotions(
        self, session: ShoppingSessionContext, category: str | None = None
    ) -> list[Promotion]:
        where = "IsActive = true AND (EndDate = null OR EndDate >= TODAY)"
        if category:
            safe = category.replace("'", "\\'")
            where += f" AND Description LIKE '%{safe}%'"
        rows = await self._soql(
            f"SELECT Id, Name, Description, StartDate, EndDate "
            f"FROM Promotion WHERE {where} ORDER BY EndDate ASC NULLS LAST LIMIT 20"
        )
        return [_row_to_promotion(r) for r in rows]

    async def apply_promotion(
        self,
        session: ShoppingSessionContext,
        promotion_id: str | None = None,
        code: str | None = None,
    ) -> Cart:
        if not promotion_id and not code:
            raise NotOffered
        account_id = await self._account_id_for_user(session.user_id)
        webstore_id = await self._ensure_webstore_id()
        _buyer_cart_id, _ = await self._get_buyer_cart_id(session.user_id, webstore_id)
        params: dict[str, str] = {}
        if account_id:
            params["effectiveAccountId"] = account_id
        body = {"couponCode": code} if code else {"promotionId": promotion_id}
        try:
            await self._b2b_request(
                "POST",
                f"/commerce/webstores/{webstore_id}/carts/{_buyer_cart_id}/coupons",
                params=params or None,
                json=body,
            )
        except httpx.HTTPStatusError as exc:
            raise NotOffered from exc
        return await self.get_cart(session)

    # ------------------------------------------------------------------
    # Subscriptions — Salesforce ServiceContract object
    # ------------------------------------------------------------------

    async def get_subscriptions(
        self, session: ShoppingSessionContext, status: str | None = None
    ) -> list[Subscription]:
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            raise NotOffered
        where = f"AccountId = '{account_id}'"
        today = datetime.now(UTC).date().isoformat()
        if status == "expiring_soon":
            ninety = (datetime.now(UTC) + timedelta(days=90)).date().isoformat()
            where += f" AND EndDate >= {today} AND EndDate <= {ninety}"
        elif status == "expired":
            where += f" AND EndDate < {today}"
        elif status == "active":
            where += f" AND StartDate <= {today} AND EndDate >= {today}"
        rows = await self._soql(
            f"SELECT Id, Name, Status, StartDate, EndDate, ContractNumber "
            f"FROM ServiceContract WHERE {where} ORDER BY EndDate ASC LIMIT 20"
        )
        today_dt = datetime.now(UTC).date()
        return [_row_to_subscription(r, today_dt) for r in rows]

    async def get_subscription_details(
        self, session: ShoppingSessionContext, subscription_id: str
    ) -> Subscription | None:
        rows = await self._soql(
            f"SELECT Id, Name, Status, StartDate, EndDate, ContractNumber "
            f"FROM ServiceContract WHERE Id = '{subscription_id}' LIMIT 1"
        )
        if not rows:
            return None
        today_dt = datetime.now(UTC).date()
        return _row_to_subscription(rows[0], today_dt)

    async def renew_subscription(
        self, session: ShoppingSessionContext, subscription_id: str
    ) -> Quote:
        sub = await self.get_subscription_details(session, subscription_id)
        if not sub:
            raise NotOffered
        account_id = await self._account_id_for_user(session.user_id)
        if not account_id:
            raise NotOffered
        pb_id = await self._standard_pricebook_id()
        opp_rows = await self._soql(
            f"SELECT Id FROM Opportunity WHERE AccountId = '{account_id}' "
            f"AND IsClosed = false ORDER BY CreatedDate DESC LIMIT 1"
        )
        opp_id = opp_rows[0]["Id"] if opp_rows else None
        headers = await self._token_headers()
        new_start = sub.end_date.date().isoformat()
        new_end = (sub.end_date + timedelta(days=365)).date().isoformat()
        renewal_payload: dict[str, Any] = {
            "Name": f"Renewal — {sub.name}",
            "Status": "Draft",
            "Description": f"Renewal of ServiceContract {subscription_id}",
            "ExpirationDate": new_end,
        }
        if opp_id:
            renewal_payload["OpportunityId"] = opp_id
        else:
            renewal_payload["AccountId"] = account_id
        if pb_id:
            renewal_payload["Pricebook2Id"] = pb_id
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}/services/data/v62.0/sobjects/Quote",
                headers={**headers, "Content-Type": "application/json"},
                json=renewal_payload,
            )
            resp.raise_for_status()
            quote_id = resp.json()["id"]
        return Quote(
            quote_id=quote_id,
            name=f"Renewal — {sub.name}",
            status=QuoteStatus.DRAFT,
            subtotal=sub.annual_value or 0,
            created_at=datetime.now(UTC),
            notes=f"Covers {new_start} to {new_end}",
        )
