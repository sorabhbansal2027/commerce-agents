"""Gemini ADK merchant API.

Uvicorn target:
    uvicorn gemini.api.main:app --app-dir examples --port 8006

Required env vars:
    GEMINI_API_KEY          Google AI Studio key  (or use GOOGLE_API_KEY)
    SFCC_INSTANCE_URL       https://zzrl-008.dx.commercecloud.salesforce.com
    SFCC_CLIENT_ID          OCAPI client ID
    SFCC_CLIENT_SECRET      OCAPI client secret
    SFCC_SITE_ID            Site ID (use - for org-level)
    SFCC_DISPLAY_SITE_ID    Human-readable site name, e.g. DreamHaus
    SFCC_INV_LIST           Inventory list ID, e.g. dreamhaus-inventory

Optional:
    GEMINI_MODEL            Model name (default: gemini-2.0-flash)
    CORS_ORIGINS            Comma-separated allowed origins
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import GeminiMerchantAgent
from .merchandising import set_merchandising_backend
from .tools import set_backend

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Session store ─────────────────────────────────────────────────────────────

_sessions: dict[str, dict[str, Any]] = {}

# ── Startup ───────────────────────────────────────────────────────────────────

_agent: GeminiMerchantAgent | None = None


def _build_backend() -> Any:
    """Build the SFCC adapter if SFCC env vars are present."""
    if not os.environ.get("SFCC_INSTANCE_URL"):
        raise RuntimeError("SFCC_INSTANCE_URL is required")
    # Reuse the same SFCCAdapter from the salesforce vertical
    import sys
    from pathlib import Path

    examples_dir = Path(__file__).parent.parent.parent
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))

    from salesforce.api.merchant import _SFCCAdapter
    from sfcc.sfcc_bm_backend import SFCCBusinessManagerBackend

    return _SFCCAdapter(SFCCBusinessManagerBackend())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    # Set GOOGLE_API_KEY from GEMINI_API_KEY if not already set
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

    backend = _build_backend()
    set_backend(backend)
    set_merchandising_backend(backend)
    store_name = os.environ.get("SFCC_DISPLAY_SITE_ID", "DreamHaus")
    _agent = GeminiMerchantAgent(store_name=store_name)
    log.info("Gemini ADK merchant agent ready for store: %s", store_name)
    yield


app = FastAPI(title="Gemini Merchant API", lifespan=lifespan)

# CORS
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials="*" not in _origins,
)

# ── Request / response models ─────────────────────────────────────────────────


class SessionResponse(BaseModel):
    session_id: str
    merchant_id: str
    store_name: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class EnrichRequest(BaseModel):
    listing_id: str
    aspects: str = "all"
    auto_stage: bool = False


class SEORequest(BaseModel):
    listing_id: str
    market: str = "US"
    brand: str = "DreamHaus"


class GeoRequest(BaseModel):
    listing_id: str
    regions: str = "US-northeast,UK"


class BulkEnrichRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=5, ge=1, le=5)


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/api/merchant/health")
async def health() -> dict:
    return {"status": "ok", "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")}


@app.post("/api/merchant/session", response_model=SessionResponse)
async def create_session() -> SessionResponse:
    session_id = secrets.token_urlsafe(24)
    store_name = os.environ.get("SFCC_DISPLAY_SITE_ID", "DreamHaus")
    _sessions[session_id] = {"created_at": time.time(), "store_name": store_name}
    if _agent:
        await _agent.ensure_session(session_id)
    return SessionResponse(
        session_id=session_id,
        merchant_id="gemini-merchant",
        store_name=store_name,
    )


def _require_session(session_id: str | None) -> str:
    if not session_id or session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing session")
    return session_id


@app.post("/api/merchant/chat")
async def chat(
    request: ChatRequest,
    x_session_id: str | None = Header(default=None),
) -> StreamingResponse:
    sid = x_session_id
    if not sid or sid not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialised")

    return StreamingResponse(
        _agent.stream_turn(sid, request.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/merchant/overview")
async def overview(x_session_id: str | None = Header(default=None)) -> dict:
    from .tools import _backend, _session

    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    if _backend is None:
        raise HTTPException(status_code=503, detail="Backend not initialised")

    sess = _session()
    snapshot_data: dict = {}
    inventory_data: list = []
    order_issues: list = []

    try:
        snap = await _backend.get_business_snapshot(sess)
        snapshot_data = snap.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        log.warning("snapshot failed: %s", exc)
        snapshot_data = {"sales": 0.0, "orders": 0, "period": "30d"}

    try:
        alerts = await _backend.get_inventory_alerts(sess)
        inventory_data = [a.model_dump(mode="json", exclude_none=True) for a in alerts]
    except Exception as exc:
        log.warning("alerts failed: %s", exc)

    try:
        issues = await _backend.get_order_issues(sess)
        order_issues = [i.model_dump(mode="json", exclude_none=True) for i in issues]
    except Exception as exc:
        log.warning("order issues failed: %s", exc)

    return {
        "snapshot": {
            **snapshot_data,
            "alerts": {
                "low_stock": len(inventory_data),
                "order_issues": len(order_issues),
                "pending_changes": 0,
            },
        },
        "needs_attention": {
            "inventory": inventory_data,
            "order_issues": order_issues,
        },
        "recent_orders": [],
        "recent_changes": [],
    }


@app.get("/api/merchant/listings")
async def listings(q: str = "", limit: int = 50, x_session_id: str | None = Header(default=None)) -> dict:
    from .tools import _backend, _session

    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    if _backend is None:
        return {"listings": []}

    try:
        results = await _backend.search_listings(_session(), q, None, min(limit, 100))
        return {"listings": [r.model_dump(mode="json", exclude_none=True) for r in results]}
    except Exception as exc:
        log.warning("listings failed: %s", exc)
        return {"listings": []}


@app.get("/api/merchant/listings/{listing_id}")
async def get_listing(listing_id: str, x_session_id: str | None = Header(default=None)) -> dict:
    from .tools import _backend, _session

    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    if _backend is None:
        raise HTTPException(status_code=503, detail="Backend not initialised")

    result = await _backend.get_listing(_session(), listing_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Listing {listing_id} not found")
    return result.model_dump(mode="json", exclude_none=True)


@app.get("/api/merchant/alerts")
async def alerts(x_session_id: str | None = Header(default=None)) -> dict:
    from .tools import _backend, _session

    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    if _backend is None:
        return {"alerts": []}

    try:
        alerts_list = await _backend.get_inventory_alerts(_session())
        return {"alerts": [a.model_dump(mode="json", exclude_none=True) for a in alerts_list]}
    except Exception as exc:
        log.warning("alerts failed: %s", exc)
        return {"alerts": []}


@app.get("/api/merchant/quote-approvals")
async def quote_approvals(x_session_id: str | None = Header(default=None)) -> dict:
    from .tools import _backend, _session

    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    if _backend is None:
        return {"quote_approvals": []}

    try:
        quotes = await _backend.get_pending_quote_approvals(_session())
        return {"quote_approvals": [q.model_dump(mode="json", exclude_none=True) for q in quotes]}
    except Exception as exc:
        log.warning("quote approvals failed: %s", exc)
        return {"quote_approvals": []}


@app.post("/api/merchant/reset")
async def reset(x_session_id: str | None = Header(default=None)) -> dict:
    if x_session_id and x_session_id in _sessions:
        _sessions.pop(x_session_id, None)
    return {"reset": True}


@app.get("/api/merchant/memory")
async def memory(x_session_id: str | None = Header(default=None)) -> dict:
    return {"facts": []}


# ── Merchandising endpoints ────────────────────────────────────────────────────


@app.post("/api/merchant/merchandising/enrich")
async def merchandising_enrich(
    request: EnrichRequest,
    x_session_id: str | None = Header(default=None),
) -> dict:
    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    from .merchandising import enrich_product

    return await enrich_product(request.listing_id, aspects=request.aspects, auto_stage=request.auto_stage)


@app.post("/api/merchant/merchandising/seo")
async def merchandising_seo(
    request: SEORequest,
    x_session_id: str | None = Header(default=None),
) -> dict:
    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    from .merchandising import generate_seo_content

    return await generate_seo_content(request.listing_id, market=request.market, brand=request.brand)


@app.post("/api/merchant/merchandising/geo")
async def merchandising_geo(
    request: GeoRequest,
    x_session_id: str | None = Header(default=None),
) -> dict:
    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    from .merchandising import generate_geo_content

    return await generate_geo_content(request.listing_id, regions=request.regions)


@app.get("/api/merchant/merchandising/classify/{listing_id}")
async def merchandising_classify(
    listing_id: str,
    x_session_id: str | None = Header(default=None),
) -> dict:
    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    from .merchandising import classify_product_ontology

    return await classify_product_ontology(listing_id)


@app.post("/api/merchant/merchandising/bulk")
async def merchandising_bulk(
    request: BulkEnrichRequest,
    x_session_id: str | None = Header(default=None),
) -> dict:
    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    from .merchandising import bulk_enrich_catalog

    return await bulk_enrich_catalog(query=request.query, limit=request.limit)


@app.get("/api/merchant/merchandising/products")
async def merchandising_products(
    x_session_id: str | None = Header(default=None),
) -> dict:
    """Return the BBW product fixture catalog for the merchandising demo UI."""
    if not x_session_id or x_session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    from gemini.ontology.bbw_products import BBW_PRODUCTS

    return {"products": BBW_PRODUCTS}
