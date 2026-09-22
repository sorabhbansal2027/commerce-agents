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
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response

_UCP_VERSION = "1.0"
_log = logging.getLogger(__name__)

# Demo buyer Salesforce user ID — set via env var on the backend service.
# Needed to locate the buyer's active WebCart when placing real B2B orders.
_SF_BUYER_USER_ID = os.environ.get("SF_BUYER_USER_ID", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base(request: Request) -> str:
    """Root URL of this server, no trailing slash."""
    return str(request.base_url).rstrip("/")


def _listing_to_ucp(listing: Any) -> dict[str, Any]:
    """Map an internal Listing/Product object to the UCP product schema."""
    product_id = getattr(listing, "listing_id", None) or getattr(listing, "product_id", "")
    # FAMILY-* are synthetic grouping IDs — not valid Salesforce Product2Ids.
    # Resolve to the first (lowest-price) variant's real product_id so that
    # cart and order operations via the B2B Commerce REST API succeed.
    if str(product_id).startswith("FAMILY-"):
        variants = getattr(listing, "variants", None) or []
        if variants:
            product_id = getattr(variants[0], "product_id", product_id)
    d: dict[str, Any] = {
        "product_id": product_id,
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
            if (
                q_lower
                and q_lower not in (p.title or "").lower()
                and q_lower not in (getattr(p, "brand", "") or "").lower()
            ):
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
                items_out.append(
                    {
                        "product_id": p.product_id
                        if hasattr(p, "product_id")
                        else getattr(p, "listing_id", ""),
                        "title": p.title,
                        "quantity": qty,
                        "unit_price": price,
                        "line_total": round(price * qty, 2),
                        "currency": getattr(p, "currency", "USD"),
                    }
                )

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
        """Retrieve a checkout session status by ID."""
        return Response(
            content=json.dumps(
                {
                    "checkout_session_id": session_id,
                    "status": "pending",
                    "note": (
                        "Reference implementation: session state is not persisted. "
                        "Integrate with your order management system for live status."
                    ),
                }
            ),
            media_type="application/json",
        )

    # ── Active cart fetch ──────────────────────────────────────────────────────

    @router.get("/ucp/cart", summary="Fetch buyer's active cart from Salesforce")
    async def ucp_get_buyer_cart(buyer_user_id: str = "", request: Request = None) -> Response:
        """Return the buyer's active Salesforce B2B WebCart items.

        ``buyer_user_id`` is the Salesforce User Id (starts with 005).
        Used on login to pre-populate the UI cart.
        """
        uid = buyer_user_id or _SF_BUYER_USER_ID
        if not uid or not hasattr(backend, "get_cart"):
            return Response(content=json.dumps({"items": []}), media_type="application/json")

        try:
            import datetime as _dt

            from shopping_agent.types import ShoppingSessionContext as _SSC

            session = _SSC(user_id=uid, now=_dt.datetime.utcnow())
            cart = await backend.get_cart(session)
            items = [
                {
                    "product_id": ci.product_id,
                    "title": ci.title,
                    "price": float(ci.price or 0),
                    "currency": getattr(cart, "currency", "USD"),
                    "image_url": getattr(ci, "image_url", None),
                    "in_stock": True,
                    "quantity": int(ci.quantity or 1),
                }
                for ci in cart.items
            ]
            return Response(content=json.dumps({"items": items}), media_type="application/json")
        except Exception as exc:
            _log.warning("ucp_get_buyer_cart failed: %s", exc)
            return Response(
                content=json.dumps({"items": [], "warning": str(exc)}),
                media_type="application/json",
            )

    # ── Agentic order placement (B2B Commerce — no storefront required) ──────

    @router.post("/ucp/orders", status_code=201, summary="Place a B2B order agentically")
    async def ucp_place_b2b_order(request: Request) -> Response:
        """Place a real Salesforce B2B Commerce order without requiring storefront interaction.

        Requires ``SF_BUYER_USER_ID`` env var set to the demo buyer's Salesforce user ID.
        Walks the full B2B checkout flow: get/create cart → add items → initiate checkout
        → placeOrder action.

        Request body::

            {
              "line_items": [{"product_id": "...", "quantity": 1, "title": "...", "unit_price": 0}],
              "payment_handler": "purchase_order",
              "buyer": {"name": "...", "email": "..."}
            }
        """
        body = await request.json()
        line_items = body.get("line_items", [])
        payment_handler = body.get("payment_handler", "purchase_order")
        buyer = body.get("buyer", {})

        # buyer_user_id comes from the logged-in user's Salesforce session (preferred);
        # fall back to the env var for backwards-compat / local dev
        buyer_uid = body.get("buyer_user_id") or _SF_BUYER_USER_ID
        if not buyer_uid:
            return Response(
                status_code=501,
                content=json.dumps(
                    {
                        "error": "No buyer user ID available. Log in via Salesforce first, "
                        "or set SF_BUYER_USER_ID on this service."
                    }
                ),
                media_type="application/json",
            )

        if not hasattr(backend, "_b2b_request") or not hasattr(backend, "_ensure_webstore_id"):
            return Response(
                status_code=501,
                content=json.dumps(
                    {"error": "Backend does not support direct B2B order placement."}
                ),
                media_type="application/json",
            )

        try:
            webstore_id = await backend._ensure_webstore_id()
            account_id = await backend._account_id_for_user(buyer_uid)

            # effectiveAccountId is required for all B2B headless cart operations.
            # Without it the integration user's context is used, which creates a cart
            # the buyer can't see and that SOQL by OwnerId will never find.
            if not account_id:
                return Response(
                    status_code=501,
                    content=json.dumps(
                        {
                            "error": f"No buyer account found for user {buyer_uid}. "
                            "The signed-in user must be an active B2B Commerce portal "
                            "user with an associated Account."
                        }
                    ),
                    media_type="application/json",
                )
            eff_params = {"effectiveAccountId": account_id}

            # ── 1. Get the buyer's active WebCart (SOQL — no explicit creation) ───
            cart_id, currency = await backend._get_buyer_cart_id(buyer_uid, webstore_id)

            # ── 2. Add each item to the cart ────────────────────────────────────
            # When no cart exists yet, /carts/active/cart-items auto-creates one.
            # Track the first failure so we can surface a meaningful error.
            placed_items: list[dict] = []
            first_item_error: str = ""
            for item in line_items:
                pid = item.get("product_id", "")
                qty = max(1, int(item.get("quantity", 1)))
                if not pid:
                    continue
                endpoint = (
                    f"/commerce/webstores/{webstore_id}/carts/{cart_id}/cart-items"
                    if cart_id
                    else f"/commerce/webstores/{webstore_id}/carts/active/cart-items"
                )
                added = False
                try:
                    await backend._b2b_request(
                        "POST",
                        endpoint,
                        params=eff_params,
                        json={"productId": pid, "quantity": str(qty), "type": "Product"},
                    )
                    # After the first item auto-creates the cart, resolve the new cart ID.
                    if not cart_id:
                        cart_id, currency = await backend._get_buyer_cart_id(buyer_uid, webstore_id)
                    # Fallback: integration-user context may set OwnerId to the integration
                    # user rather than the buyer, so also search by AccountId.
                    if not cart_id and account_id:
                        rows = await backend._soql(
                            f"SELECT Id, CurrencyIsoCode FROM WebCart "
                            f"WHERE AccountId = '{account_id}' AND Status = 'Active' "
                            f"AND WebStoreId = '{webstore_id}' "
                            f"ORDER BY LastModifiedDate DESC LIMIT 1"
                        )
                        if rows:
                            cart_id = rows[0].get("Id", "")
                            currency = rows[0].get("CurrencyIsoCode", "USD")
                    added = True
                except Exception as item_exc:
                    if not first_item_error:
                        first_item_error = str(item_exc)
                    if cart_id:
                        # B2B search-index miss — fall back to direct CartItem SObject insert
                        try:
                            await backend._add_to_cart_direct(cart_id, pid, qty)
                            added = True
                        except Exception:
                            pass
                if added:
                    placed_items.append(
                        {
                            "product_id": pid,
                            "title": item.get("title", pid),
                            "quantity": qty,
                            "unit_price": float(item.get("unit_price", 0)),
                            "line_total": round(float(item.get("unit_price", 0)) * qty, 2),
                            "currency": currency,
                        }
                    )

            if not cart_id:
                error_msg = "Could not locate or create B2B cart — no items were added."
                if first_item_error:
                    error_msg += f" Detail: {first_item_error}"
                return Response(
                    status_code=500,
                    content=json.dumps({"error": error_msg}),
                    media_type="application/json",
                )

            # ── 3. Place the order via /commerce/sale/order ─────────────────────
            # This single API handles the full checkout flow (pricing, tax, shipping)
            # and creates the Salesforce Order in one call — no intermediate checkout
            # session resource is needed.
            order_body: dict = {"cartId": cart_id}
            if account_id:
                order_body["effectiveAccountId"] = account_id
            if po_number := body.get("po_number", ""):
                order_body["poNumber"] = po_number
            order_resp = await backend._b2b_request(
                "POST",
                "/commerce/sale/order",
                json=order_body,
            )
            order_id = (
                order_resp.get("orderId")
                or order_resp.get("orderReferenceNumber")
                or order_resp.get("orderNumber")
                or order_resp.get("Id", "")
            )

            subtotal = round(sum(i["line_total"] for i in placed_items), 2)
            payload = {
                "order_id": order_id or f"ORD-{uuid.uuid4().hex[:8].upper()}",
                "status": order_resp.get("status", "placed"),
                "line_items": placed_items,
                "subtotal": subtotal,
                "currency": currency,
                "payment_handler": payment_handler,
                "po_number": body.get("po_number") or None,
                "buyer": buyer,
                "agent_note": "Order placed via /commerce/sale/order. No storefront action required.",
            }
            return Response(
                content=json.dumps(payload), status_code=201, media_type="application/json"
            )

        except Exception as exc:
            _log.exception("ucp_place_b2b_order failed")
            return Response(
                status_code=500,
                content=json.dumps({"error": str(exc)}),
                media_type="application/json",
            )

    return router
