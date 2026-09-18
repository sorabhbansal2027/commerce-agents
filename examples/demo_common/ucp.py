# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Universal Commerce Protocol (UCP) manifest and product-discovery endpoints.

Exposes ``GET /.well-known/ucp`` so UCP-compatible AI agents (Gemini, etc.) can
discover this storefront's capabilities, then drives commerce through the standard
REST primitives the spec defines.

Reference: https://developers.googleblog.com/under-the-hood-universal-commerce-protocol-ucp/

Mount this router at the app root (no prefix) — the ``.well-known`` path must be at
the domain root per RFC 8615::

    app.include_router(build_ucp_router(backend))

"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response

_UCP_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base(request: Request) -> str:
    """Root URL of this server, no trailing slash."""
    return str(request.base_url).rstrip("/")


def _listing_to_ucp(listing: Any) -> dict[str, Any]:
    """Map an internal Listing/Product object to the UCP product schema."""
    d: dict[str, Any] = {
        "product_id": getattr(listing, "listing_id", None) or getattr(listing, "product_id", ""),
        "title": getattr(listing, "title", ""),
        "price": getattr(listing, "price", 0.0),
        "currency": getattr(listing, "currency", "USD"),
        "in_stock": getattr(listing, "in_stock", True),
    }
    if brand := getattr(listing, "brand", None):
        d["brand"] = brand
    if image := getattr(listing, "image_url", None):
        d["image_url"] = image
    if rating := getattr(listing, "rating", None):
        d["rating"] = rating
    if category := getattr(listing, "category", None):
        d["category"] = category
    return d


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def build_ucp_manifest(backend: Any, base_url: str) -> dict[str, Any]:
    """Return the UCP manifest dict for this storefront."""
    store_name = getattr(backend, "store_name", "ACME Commerce")

    return {
        "schema_version": _UCP_VERSION,
        "business": {
            "name": store_name,
            "description": (
                "B2B and B2C commerce storefront powered by a Claude shopping agent. "
                "Supports product search, cart management, and checkout initiation."
            ),
        },
        "capabilities": {
            "product_discovery": {
                "supported": True,
                "endpoints": {
                    "search": f"{base_url}/ucp/products",
                    "detail": f"{base_url}/ucp/products/{{product_id}}",
                },
                "features": [
                    "full_text_search",
                    "category_filter",
                    "price_range_filter",
                    "availability_filter",
                ],
                "pagination": {"supported": True, "max_limit": 50},
            },
            "cart_management": {
                "supported": True,
                "note": (
                    "Cart writes require a session. POST /ucp/sessions first, "
                    "then pass X-Session-Id on subsequent requests."
                ),
                "endpoints": {
                    "create_session": f"{base_url}/ucp/sessions",
                    "get_cart": f"{base_url}/ucp/cart",
                    "add_item": f"{base_url}/ucp/cart/items",
                },
            },
            "checkout": {
                "supported": True,
                "endpoints": {
                    "create_session": f"{base_url}/ucp/checkout-sessions",
                    "get_session": f"{base_url}/ucp/checkout-sessions/{{session_id}}",
                },
                "note": (
                    "Checkout sessions are pending confirmations — no charge is made "
                    "here. The buyer completes the purchase through the storefront."
                ),
            },
        },
        "payment_handlers": [
            {
                "id": "credit_card",
                "name": "Credit Card",
                "supported_networks": ["visa", "mastercard", "amex"],
            },
            {
                "id": "purchase_order",
                "name": "Purchase Order (B2B)",
                "supported": True,
            },
        ],
        "transport": {
            "rest": {
                "base_url": base_url,
                "auth": "UCP-Agent",
                "idempotency_key_header": "Idempotency-Key",
            },
            "mcp": {
                "description": (
                    "A richer Claude shopping agent is available over MCP with memory, "
                    "policy search, fulfillment options, and interactive UI resources."
                ),
                "protocol": "streamable-http",
                "note": "Set STOREFRONT_MCP_URL in your environment to locate the MCP server.",
            },
        },
        "agent_hints": (
            "Prefer the MCP transport for complex shopping journeys. "
            "Use REST for single-turn product lookups and checkout session creation."
        ),
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def build_ucp_router(backend: Any) -> APIRouter:
    """Return an ``APIRouter`` with the UCP manifest and core commerce endpoints.

    Mount at the app root (no prefix) so ``/.well-known/ucp`` resolves correctly.
    All product endpoints are sessionless; cart writes return a 501 stub pointing
    agents at the session-aware MCP transport for writes.
    """
    router = APIRouter(tags=["UCP"])

    # ── Discovery manifest ─────────────────────────────────────────────────

    @router.get("/.well-known/ucp", summary="UCP capability manifest")
    async def ucp_manifest(request: Request) -> Response:
        """UCP discovery: returns the manifest of supported commerce capabilities."""
        manifest = build_ucp_manifest(backend, _base(request))
        return Response(
            content=json.dumps(manifest, indent=2),
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=300"},
        )

    # ── Product discovery (sessionless) ───────────────────────────────────

    @router.get("/ucp/products", summary="UCP product search")
    async def ucp_search_products(
        request: Request,
        q: str = "",
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        in_stock: bool = True,
        limit: int = 10,
    ) -> Response:
        """UCP product search endpoint. Sessionless — no auth required."""
        limit = max(1, min(limit, 50))
        all_products = list(backend.products.values())

        # Filter
        results = []
        q_lower = q.lower()
        for p in all_products:
            if q_lower and q_lower not in (p.title or "").lower() and q_lower not in (getattr(p, "brand", "") or "").lower():
                continue
            if category and getattr(p, "category", None) != category:
                continue
            price = float(p.price or 0)
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            if in_stock and not getattr(p, "in_stock", True):
                continue
            results.append(p)
            if len(results) >= limit:
                break

        payload = {
            "products": [_listing_to_ucp(p) for p in results],
            "total": len(results),
            "query": q or None,
        }
        return Response(content=json.dumps(payload), media_type="application/json")

    @router.get("/ucp/products/{product_id:path}", summary="UCP product detail")
    async def ucp_get_product(product_id: str, request: Request) -> Response:
        """UCP product detail. ``product_id`` may contain slashes."""
        p = backend.product(product_id)
        if p is None:
            return Response(
                status_code=404,
                content=json.dumps({"error": "product_not_found", "product_id": product_id}),
                media_type="application/json",
            )
        return Response(content=json.dumps(_listing_to_ucp(p)), media_type="application/json")

    # ── Checkout session ───────────────────────────────────────────────────

    @router.post("/ucp/checkout-sessions", status_code=201, summary="UCP checkout session")
    async def ucp_create_checkout_session(request: Request) -> Response:
        """Create a UCP checkout session from a list of line items.

        Request body::

            {
              "line_items": [{"product_id": "...", "quantity": 1}],
              "buyer": {"name": "...", "email": "..."},
              "payment_handler": "credit_card"
            }

        Returns a pending checkout session. No charge is made here.
        """
        body = await request.json()
        line_items = body.get("line_items", [])
        session_id = f"ucp-{uuid.uuid4().hex[:12]}"

        total = 0.0
        items_out = []
        for item in line_items:
            p = backend.product(item.get("product_id", ""))
            if p is not None:
                price = float(p.price or 0)
                qty = max(1, int(item.get("quantity", 1)))
                total += price * qty
                items_out.append({
                    "product_id": p.product_id if hasattr(p, "product_id") else getattr(p, "listing_id", ""),
                    "title": p.title,
                    "quantity": qty,
                    "unit_price": price,
                    "line_total": round(price * qty, 2),
                    "currency": getattr(p, "currency", "USD"),
                })

        payload = {
            "checkout_session_id": session_id,
            "status": "pending",
            "line_items": items_out,
            "subtotal": round(total, 2),
            "currency": "USD",
            "payment_handler": body.get("payment_handler", "credit_card"),
            "buyer": body.get("buyer", {}),
            "agent_note": (
                "This UCP checkout session is pending buyer confirmation. "
                "No payment has been charged. The buyer must complete checkout "
                "through the storefront UI or agent."
            ),
        }
        return Response(
            content=json.dumps(payload),
            media_type="application/json",
            status_code=201,
        )

    @router.get("/ucp/checkout-sessions/{session_id}", summary="UCP checkout session status")
    async def ucp_get_checkout_session(session_id: str, request: Request) -> Response:
        """Retrieve a checkout session status by ID.

        In this reference implementation sessions are not persisted; production
        backends should store them in a durable store and return the live status.
        """
        return Response(
            content=json.dumps({
                "checkout_session_id": session_id,
                "status": "pending",
                "note": (
                    "Reference implementation: session state is not persisted. "
                    "Integrate with your order management system for live status."
                ),
            }),
            media_type="application/json",
        )

    return router
