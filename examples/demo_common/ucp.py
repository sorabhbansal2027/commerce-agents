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

import contextlib
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
            "Use REST for single-turn product lookups and checkout session creation. "
            "An ontology layer normalises synonyms and infers categories automatically: "
            "terms such as 'notebook', 'ultrabook', 'smartphone', 'cell phone', 'headset', "
            "'earbuds', 'SSD', 'hard drive', and 'DSLR' are resolved to their canonical "
            "forms before each search, and a matching category filter is added when none "
            "is supplied by the caller."
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
        buyer_session_id   = (body.get("buyer_session_id")   or "").strip()
        buyer_instance_url = (body.get("buyer_instance_url") or "").strip()
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
            # Prefer account_id supplied at login (from buyer/login response) to
            # avoid an extra SOQL round-trip on every order.
            account_id = body.get("buyer_account_id") or await backend._account_id_for_user(
                buyer_uid
            )

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

            # When the buyer logged in via SOAP we have their session token and can
            # call B2B Commerce APIs directly as that user — no effectiveAccountId needed.
            # Fall back to admin client-credentials + effectiveAccountId when no session.
            buyer_auth_kwargs: dict = {}
            if buyer_session_id:
                buyer_auth_kwargs = {
                    "auth_token": buyer_session_id,
                    **({"instance_url": buyer_instance_url} if buyer_instance_url else {}),
                }
            req_params = {} if buyer_session_id else eff_params

            # ── 1. Find or create an Active cart for this account ───────────────
            # Primary: look up by AccountId (OwnerId may differ for portal users).
            # If only Checkout-status carts exist they block new cart creation, so
            # close them first before creating a fresh one.
            cart_id = ""
            currency = "USD"
            all_open_rows = await backend._soql(
                f"SELECT Id, Status, CurrencyIsoCode FROM WebCart "
                f"WHERE AccountId = '{account_id}' AND WebStoreId = '{webstore_id}' "
                f"AND Status IN ('Active', 'Checkout') "
                f"ORDER BY LastModifiedDate DESC LIMIT 10"
            )
            active_rows = [r for r in all_open_rows if r.get("Status") == "Active"]
            checkout_rows = [r for r in all_open_rows if r.get("Status") == "Checkout"]

            if active_rows:
                cart_id = active_rows[0]["Id"]
                currency = active_rows[0].get("CurrencyIsoCode", "USD")
            else:
                # Close any stuck Checkout-status carts so we can create a fresh one.
                for old in checkout_rows:
                    with contextlib.suppress(Exception):
                        await backend._b2b_request(
                            "PATCH",
                            f"/sobjects/WebCart/{old['Id']}",
                            **buyer_auth_kwargs,
                            json={"Status": "Closed"},
                        )
                # Create a new cart for the buyer account.
                new_cart = await backend._b2b_request(
                    "POST",
                    f"/commerce/webstores/{webstore_id}/carts",
                    **buyer_auth_kwargs,
                    params=req_params,
                    json={},
                )
                cart_id = new_cart.get("cartId") or new_cart.get("id") or new_cart.get("Id", "")
                currency = new_cart.get("currencyIsoCode", "USD")

            if not cart_id:
                return Response(
                    status_code=500,
                    content=json.dumps({"error": "Could not locate or create a B2B cart."}),
                    media_type="application/json",
                )

            # ── 2. Add each item to the cart ────────────────────────────────────
            # B2B Commerce cart-items API requires quantity as an integer and the
            # "type" field.  Direct CartItem insert is used as fallback for products
            # not yet in the search index (returns NOT_FOUND, not 400).
            placed_items: list[dict] = []
            first_item_error: str = ""
            for item in line_items:
                pid = item.get("product_id", "")
                qty = max(1, int(item.get("quantity", 1)))
                if not pid:
                    continue
                added = False
                try:
                    await backend._b2b_request(
                        "POST",
                        f"/commerce/webstores/{webstore_id}/carts/{cart_id}/cart-items",
                        **buyer_auth_kwargs,
                        params=req_params,
                        json={"productId": pid, "quantity": qty, "type": "Product"},
                    )
                    added = True
                except Exception as item_exc:
                    if not first_item_error:
                        first_item_error = str(item_exc)
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

            if not placed_items:
                error_msg = "No items could be added to the cart."
                if first_item_error:
                    error_msg += f" Detail: {first_item_error}"
                return Response(
                    status_code=500,
                    content=json.dumps({"error": error_msg}),
                    media_type="application/json",
                )

            # ── 3. Initiate checkout via B2B Commerce checkout API ───────────────
            # POST /checkouts is the canonical B2B Commerce Checkout API endpoint.
            # effectiveAccountId goes in the body (not query param) for this call.
            po_number = body.get("po_number", "")
            checkout_body: dict = {"cartReference": {"id": cart_id}}
            if not buyer_session_id and account_id:
                checkout_body["effectiveAccountId"] = account_id
            checkout_resp = await backend._b2b_request(
                "POST",
                f"/commerce/webstores/{webstore_id}/checkouts",
                **buyer_auth_kwargs,
                json=checkout_body,
            )
            checkout_id: str = (
                checkout_resp.get("checkoutId")
                or checkout_resp.get("cartId")
                or cart_id
            )

            # ── 4. Set payment details on the checkout ────────────────────────────
            # PATCH the checkout to record the payment method and optional PO number.
            patch_body: dict[str, Any] = {}
            if payment_handler == "purchase_order" and po_number:
                patch_body["poNumber"] = po_number
            if patch_body:
                try:
                    await backend._b2b_request(
                        "PATCH",
                        f"/commerce/webstores/{webstore_id}/checkouts/{checkout_id}",
                        **buyer_auth_kwargs,
                        params=req_params,
                        json=patch_body,
                    )
                except Exception as patch_exc:
                    _log.warning("Checkout PATCH failed (continuing): %s", patch_exc)

            # ── 5. Place the order via B2B Commerce place-order action ─────────────
            # POST .../checkouts/{checkoutId}/actions/place-order submits the order,
            # closes the cart, and returns the Salesforce Order record details.
            place_resp = await backend._b2b_request(
                "POST",
                f"/commerce/webstores/{webstore_id}/checkouts/{checkout_id}/actions/place-order",
                **buyer_auth_kwargs,
                params=req_params,
                json={},
            )
            sf_order_id: str = (
                place_resp.get("orderId")
                or place_resp.get("orderSummaryId")
                or place_resp.get("id")
                or ""
            )
            order_number: str = (
                place_resp.get("orderNumber")
                or place_resp.get("orderReferenceNumber")
                or sf_order_id
            )

            subtotal = round(sum(i["line_total"] for i in placed_items), 2)
            payload = {
                "order_id": order_number,
                "salesforce_order_id": sf_order_id,
                "status": "placed",
                "line_items": placed_items,
                "subtotal": subtotal,
                "currency": currency,
                "payment_handler": payment_handler,
                "po_number": po_number or None,
                "buyer": buyer,
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
