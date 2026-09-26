"""Gemini ADK storefront API — wired to Salesforce B2B Commerce.

Uvicorn target:
    uvicorn gemini.api.storefront_main:app --app-dir examples --port 8007

Required env vars:
    GEMINI_API_KEY          Google AI Studio key  (or use GOOGLE_API_KEY)
    SF_INSTANCE_URL         https://<org>.sandbox.my.salesforce.com
    SF_CLIENT_ID            Connected App consumer key
    SF_CLIENT_SECRET        Connected App consumer secret

Optional:
    SF_STORE_NAME           Display name shown in UI (default: ACME B2B Store)
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

from .storefront_agent import GeminiStorefrontAgent
from .storefront_tools import _cart_payload, _carts, set_backend

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Session store ─────────────────────────────────────────────────────────────

_sessions: dict[str, dict[str, Any]] = {}

# ── Agent (one per deployment) ────────────────────────────────────────────────

_agent: GeminiStorefrontAgent | None = None


def _build_backend() -> Any:
    if not os.environ.get("SF_INSTANCE_URL"):
        raise RuntimeError("SF_INSTANCE_URL is required")
    import sys
    from pathlib import Path

    examples_dir = Path(__file__).parent.parent.parent
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))

    from salesforce.api.sf_oms_backend import SalesforceOMSBackend

    return SalesforceOMSBackend()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

    backend = _build_backend()
    set_backend(backend)
    store_name = os.environ.get("SF_STORE_NAME", "ACME B2B Store")
    _agent = GeminiStorefrontAgent(store_name=store_name)
    log.info("Gemini ADK storefront agent ready for store: %s", store_name)
    yield


app = FastAPI(title="Gemini Storefront API", lifespan=lifespan)

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials="*" not in _origins,
)

# ── Request/response models ───────────────────────────────────────────────────


class StartSessionRequest(BaseModel):
    user_id: str = Field(default="guest", min_length=1, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class CartAddRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=1, ge=1)


class ResetRequest(BaseModel):
    clear_memory: bool = False
    purge_memory: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_session(session_id: str | None) -> str:
    if not session_id or session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Session-Id header")
    return session_id


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "store": os.environ.get("SF_STORE_NAME", "ACME B2B Store"),
        "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
    }


@app.post("/api/session")
async def start_session(request: StartSessionRequest | None = None) -> dict:
    req = request or StartSessionRequest()
    session_id = secrets.token_urlsafe(24)
    _sessions[session_id] = {"user_id": req.user_id, "created_at": time.time()}
    if _agent:
        await _agent.ensure_session(session_id)
    return {
        "session_id": session_id,
        "user_id": req.user_id,
        "name": "Guest",
        "tier": None,
    }


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    x_session_id: str | None = Header(default=None),
) -> StreamingResponse:
    sid = _require_session(x_session_id)
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    return StreamingResponse(
        _agent.stream_turn(sid, request.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/products")
async def list_products(category: str = "", limit: int = 24) -> dict:
    from .storefront_tools import _backend, _product_to_dict, _sf_ctx

    if _backend is None:
        return {"products": []}
    try:
        from shopping_agent import SearchFilters
        ctx = _sf_ctx()
        filters = SearchFilters(category=category) if category else None
        products = await _backend.search_products(
            ctx, "", filters, limit=max(1, min(limit, 100))
        )
        return {"products": [_product_to_dict(p) for p in products]}
    except Exception as exc:
        log.warning("list_products failed: %s", exc)
        return {"products": []}


@app.get("/api/products/{product_id:path}")
async def get_product(product_id: str) -> dict:
    from .storefront_tools import _backend, _product_to_dict, _sf_ctx

    if _backend is None:
        raise HTTPException(status_code=503, detail="Backend not initialised")
    try:
        ctx = _sf_ctx()
        p = await _backend.get_product_details(ctx, product_id)
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")
        return _product_to_dict(p)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("get_product %s failed: %s", product_id, exc)
        raise HTTPException(status_code=404, detail="Product not found") from exc


@app.get("/api/cart")
async def get_cart(x_session_id: str | None = Header(default=None)) -> dict:
    sid = _require_session(x_session_id)
    return _cart_payload(sid)


@app.post("/api/cart/add")
async def cart_add(
    request: CartAddRequest,
    x_session_id: str | None = Header(default=None),
) -> dict:
    from .storefront_tools import _backend, _product_to_dict, _sf_ctx, add_to_cart

    sid = _require_session(x_session_id)
    product: dict[str, Any] = {}
    if _backend:
        try:
            ctx = _sf_ctx()
            p = await _backend.get_product_details(ctx, request.product_id)
            if p:
                product = _product_to_dict(p)
        except Exception as exc:
            log.warning("cart_add product lookup failed: %s", exc)
    if not product:
        raise HTTPException(status_code=400, detail="Product not found")
    result = add_to_cart(
        session_id=sid,
        product_id=request.product_id,
        title=product.get("title", request.product_id),
        price=product.get("price", 0.0),
        quantity=request.quantity,
        image_url=product.get("image_url") or "",
    )
    return {"ok": True, "cart": result["cart"]}


@app.get("/api/orders")
async def list_orders(x_session_id: str | None = Header(default=None)) -> dict:
    _require_session(x_session_id)
    return {"orders": []}


@app.get("/api/memory")
async def get_memory(x_session_id: str | None = Header(default=None)) -> dict:
    _require_session(x_session_id)
    return {"facts": []}


@app.patch("/api/memory")
async def edit_memory(x_session_id: str | None = Header(default=None)) -> dict:
    _require_session(x_session_id)
    raise HTTPException(status_code=501, detail="Memory editing not supported in this demo")


@app.delete("/api/memory")
async def delete_memory(x_session_id: str | None = Header(default=None)) -> dict:
    _require_session(x_session_id)
    return {"ok": True}


@app.post("/api/reset")
async def reset(
    request: ResetRequest | None = None,
    x_session_id: str | None = Header(default=None),
) -> dict:
    if x_session_id and x_session_id in _sessions:
        _sessions.pop(x_session_id, None)
        _carts.pop(x_session_id, None)
        if _agent:
            _agent.drop_session(x_session_id)
    new_sid = secrets.token_urlsafe(24)
    _sessions[new_sid] = {"user_id": "guest", "created_at": time.time()}
    if _agent:
        await _agent.ensure_session(new_sid)
    return {"ok": True, "session_id": new_sid}
