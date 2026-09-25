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

    async def _resolve_buyer_params(body: dict) -> tuple[str, str, dict, dict, dict]:
        """Return (buyer_uid, account_id, eff_params, buyer_auth_kwargs, req_params).

        Raises ValueError with a user-friendly message when required fields are missing.
        """
        buyer_uid = body.get("buyer_user_id") or _SF_BUYER_USER_ID
        if not buyer_uid:
            raise ValueError(
                "No buyer user ID. Log in via Salesforce first or set SF_BUYER_USER_ID."
            )
        account_id = body.get("buyer_account_id") or ""
        if not account_id and hasattr(backend, "_account_id_for_user"):
            account_id = (await backend._account_id_for_user(buyer_uid)) or ""
        if not account_id:
            raise ValueError(
                f"No buyer account found for user {buyer_uid}. "
                "The user must be an active B2B Commerce portal user."
            )
        eff_params = {"effectiveAccountId": account_id}
        buyer_session_id = (body.get("buyer_session_id") or "").strip()
        buyer_instance_url = (body.get("buyer_instance_url") or "").strip()
        buyer_auth_kwargs: dict = {}
        if buyer_session_id:
            buyer_auth_kwargs = {
                "auth_token": buyer_session_id,
                **({"instance_url": buyer_instance_url} if buyer_instance_url else {}),
            }
        req_params = {} if buyer_session_id else eff_params
        return buyer_uid, account_id, eff_params, buyer_auth_kwargs, req_params

    async def _get_or_create_cart(
        backend: Any, webstore_id: str, account_id: str, buyer_auth_kwargs: dict, req_params: dict
    ) -> tuple[str, str]:
        """Return (cart_id, currency) for the buyer's active cart, creating one if needed."""
        all_open_rows = await backend._soql(
            f"SELECT Id, Status, CurrencyIsoCode FROM WebCart "
            f"WHERE AccountId = '{account_id}' AND WebStoreId = '{webstore_id}' "
            f"AND Status IN ('Active', 'Checkout') "
            f"ORDER BY LastModifiedDate DESC LIMIT 10"
        )
        active_rows = [r for r in all_open_rows if r.get("Status") == "Active"]
        checkout_rows = [r for r in all_open_rows if r.get("Status") == "Checkout"]

        if active_rows:
            return active_rows[0]["Id"], active_rows[0].get("CurrencyIsoCode", "USD")

        for old in checkout_rows:
            with contextlib.suppress(Exception):
                await backend._b2b_request(
                    "PATCH", f"/sobjects/WebCart/{old['Id']}",
                    **buyer_auth_kwargs, json={"Status": "Closed"},
                )
        new_cart = await backend._b2b_request(
            "POST", f"/commerce/webstores/{webstore_id}/carts",
            **buyer_auth_kwargs, params=req_params, json={},
        )
        cart_id = new_cart.get("cartId") or new_cart.get("id") or new_cart.get("Id", "")
        currency = new_cart.get("currencyIsoCode", "USD")
        return cart_id, currency

    async def _add_items_to_cart(
        backend: Any, webstore_id: str, cart_id: str,
        line_items: list, buyer_auth_kwargs: dict, req_params: dict, currency: str,
    ) -> tuple[list, str]:
        """Add line_items to cart_id. Returns (placed_items, first_error)."""
        placed_items: list[dict] = []
        first_error = ""
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
                if not first_error:
                    first_error = str(item_exc)
                try:
                    await backend._add_to_cart_direct(cart_id, pid, qty)
                    added = True
                except Exception:
                    pass
            if added:
                placed_items.append({
                    "product_id": pid,
                    "title": item.get("title", pid),
                    "quantity": qty,
                    "unit_price": float(item.get("unit_price", 0)),
                    "line_total": round(float(item.get("unit_price", 0)) * qty, 2),
                    "currency": currency,
                })
        return placed_items, first_error

    @router.post("/ucp/checkout-sessions", status_code=201, summary="Create B2B checkout session")
    async def ucp_create_checkout_session(request: Request) -> Response:
        """Create Salesforce WebCart with line items and initiate a B2B checkout session.

        Request body::

            {
              "line_items": [{"product_id": "...", "quantity": 1, "title": "...", "unit_price": 0}],
              "payment_handler": "purchase_order",
              "buyer_user_id": "005...",
              "buyer_account_id": "001...",
              "buyer_session_id": "",
              "buyer_instance_url": ""
            }

        Returns ``{checkout_session_id, cart_id, delivery_group_id, ...}`` for use in
        subsequent PATCH (address) and place-order steps.
        """
        if not hasattr(backend, "_b2b_request") or not hasattr(backend, "_ensure_webstore_id"):
            return Response(
                status_code=501,
                content=json.dumps({"error": "Backend does not support B2B checkout."}),
                media_type="application/json",
            )
        body = await request.json()
        line_items = body.get("line_items", [])
        try:
            buyer_uid, account_id, eff_params, buyer_auth_kwargs, req_params = (
                await _resolve_buyer_params(body)
            )
            webstore_id = await backend._ensure_webstore_id()
            cart_id, currency = await _get_or_create_cart(
                backend, webstore_id, account_id, buyer_auth_kwargs, req_params
            )
            if not cart_id:
                return Response(status_code=500,
                    content=json.dumps({"error": "Could not locate or create a B2B cart."}),
                    media_type="application/json")

            placed_items, first_error = await _add_items_to_cart(
                backend, webstore_id, cart_id, line_items, buyer_auth_kwargs, req_params, currency
            )
            if not placed_items:
                err = "No items could be added to the cart."
                if first_error:
                    err += f" Detail: {first_error}"
                return Response(status_code=500,
                    content=json.dumps({"error": err}), media_type="application/json")

            # POST /checkouts to initiate the B2B checkout session.
            checkout_body: dict = {"cartReference": {"id": cart_id}}
            if not buyer_auth_kwargs and account_id:
                checkout_body["effectiveAccountId"] = account_id
            checkout_resp = await backend._b2b_request(
                "POST", f"/commerce/webstores/{webstore_id}/checkouts",
                **buyer_auth_kwargs, json=checkout_body,
            )
            checkout_id = (
                checkout_resp.get("checkoutId")
                or checkout_resp.get("cartId")
                or cart_id
            )
            # Extract the delivery group ID so the caller can include it in the
            # address PATCH without an extra round-trip.
            dg_records = checkout_resp.get("deliveryGroups", {}).get("records", [])
            delivery_group_id = dg_records[0].get("id", "") if dg_records else ""

            subtotal = round(sum(i["line_total"] for i in placed_items), 2)
            return Response(
                content=json.dumps({
                    "checkout_session_id": checkout_id,
                    "cart_id": cart_id,
                    "delivery_group_id": delivery_group_id,
                    "status": "pending",
                    "line_items": placed_items,
                    "subtotal": subtotal,
                    "currency": currency,
                    "payment_handler": body.get("payment_handler", "purchase_order"),
                }),
                media_type="application/json",
                status_code=201,
            )
        except ValueError as exc:
            return Response(status_code=400,
                content=json.dumps({"error": str(exc)}), media_type="application/json")
        except Exception:
            _log.exception("ucp_create_checkout_session failed")
            return Response(status_code=500,
                content=json.dumps({"error": "Checkout session creation failed."}),
                media_type="application/json")

    @router.patch("/ucp/checkout-sessions/{session_id}", summary="Set address on checkout session")
    async def ucp_update_checkout_session(session_id: str, request: Request) -> Response:
        """Set shipping address, billing address, and payment details on a checkout session.

        Request body::

            {
              "shipping_address": {"name": "...", "street": "...", "city": "...",
                                   "state": "...", "postalCode": "...", "country": "US"},
              "billing_address": {"name": "...", ...},   // defaults to shipping_address
              "po_number": "PO-12345",
              "delivery_group_id": "0lb...",
              "buyer_account_id": "001...",
              "buyer_session_id": ""
            }
        """
        if not hasattr(backend, "_b2b_request") or not hasattr(backend, "_ensure_webstore_id"):
            return Response(status_code=501,
                content=json.dumps({"error": "Backend does not support B2B checkout."}),
                media_type="application/json")
        body = await request.json()
        shipping_address: dict = body.get("shipping_address") or {}
        billing_address: dict = body.get("billing_address") or shipping_address
        po_number: str = body.get("po_number", "")
        delivery_group_id: str = body.get("delivery_group_id", "")
        account_id: str = body.get("buyer_account_id", "")
        buyer_session_id: str = (body.get("buyer_session_id") or "").strip()
        buyer_instance_url: str = (body.get("buyer_instance_url") or "").strip()

        buyer_auth_kwargs: dict = {}
        if buyer_session_id:
            buyer_auth_kwargs = {
                "auth_token": buyer_session_id,
                **({"instance_url": buyer_instance_url} if buyer_instance_url else {}),
            }
        req_params: dict = {} if buyer_session_id else ({"effectiveAccountId": account_id} if account_id else {})

        try:
            webstore_id = await backend._ensure_webstore_id()
            patch_body: dict[str, Any] = {}
            if po_number:
                patch_body["poNumber"] = po_number
            # Set shipping address on the delivery group.
            if shipping_address and delivery_group_id:
                patch_body["deliveryGroups"] = {
                    "records": [{"id": delivery_group_id, "deliveryAddress": shipping_address}]
                }
            # Set billing address on the payment method.
            if billing_address:
                patch_body["paymentMethod"] = {"billingAddress": billing_address}
            if not patch_body:
                return Response(content=json.dumps({"ok": True, "note": "Nothing to patch."}),
                    media_type="application/json")
            await backend._b2b_request(
                "PATCH", f"/commerce/webstores/{webstore_id}/checkouts/{session_id}",
                **buyer_auth_kwargs, params=req_params, json=patch_body,
            )
            return Response(content=json.dumps({"ok": True, "checkout_session_id": session_id}),
                media_type="application/json")
        except Exception as exc:
            _log.warning("ucp_update_checkout_session failed: %s", exc)
            return Response(status_code=500,
                content=json.dumps({"error": f"Address update failed: {exc}"}),
                media_type="application/json")

    @router.post("/ucp/checkout-sessions/{session_id}/place-order", status_code=201,
                 summary="Place order from checkout session")
    async def ucp_place_order_from_session(session_id: str, request: Request) -> Response:
        """Place a B2B Commerce order from an existing checkout session.

        Request body::

            {
              "buyer_user_id": "005...",
              "buyer_account_id": "001...",
              "buyer_session_id": "",
              "buyer_instance_url": ""
            }

        Returns the placed order details.
        """
        if not hasattr(backend, "_b2b_request") or not hasattr(backend, "_ensure_webstore_id"):
            return Response(status_code=501,
                content=json.dumps({"error": "Backend does not support B2B checkout."}),
                media_type="application/json")
        body = await request.json()
        try:
            buyer_uid, account_id, eff_params, buyer_auth_kwargs, req_params = (
                await _resolve_buyer_params(body)
            )
            webstore_id = await backend._ensure_webstore_id()
            place_resp = await backend._b2b_request(
                "POST",
                f"/commerce/webstores/{webstore_id}/checkouts/{session_id}/actions/place-order",
                **buyer_auth_kwargs, params=req_params, json={},
            )
            sf_order_id = (
                place_resp.get("orderId") or place_resp.get("orderSummaryId")
                or place_resp.get("id") or ""
            )
            order_number = (
                place_resp.get("orderNumber") or place_resp.get("orderReferenceNumber")
                or sf_order_id
            )
            return Response(
                content=json.dumps({
                    "order_id": order_number,
                    "salesforce_order_id": sf_order_id,
                    "status": "placed",
                    "checkout_session_id": session_id,
                }),
                media_type="application/json",
                status_code=201,
            )
        except ValueError as exc:
            return Response(status_code=400,
                content=json.dumps({"error": str(exc)}), media_type="application/json")
        except Exception as exc:
            _log.exception("ucp_place_order_from_session failed")
            return Response(status_code=500,
                content=json.dumps({"error": f"Place order failed: {exc}"}),
                media_type="application/json")

    @router.get("/ucp/checkout-sessions/{session_id}", summary="UCP checkout session status")
    async def ucp_get_checkout_session(session_id: str, request: Request) -> Response:
        """Retrieve checkout session status. Proxies to SF B2B checkout resource."""
        if not hasattr(backend, "_b2b_request") or not hasattr(backend, "_ensure_webstore_id"):
            return Response(
                content=json.dumps({"checkout_session_id": session_id, "status": "pending"}),
                media_type="application/json",
            )
        try:
            webstore_id = await backend._ensure_webstore_id()
            data = await backend._b2b_request(
                "GET", f"/commerce/webstores/{webstore_id}/checkouts/{session_id}",
            )
            data["checkout_session_id"] = session_id
            return Response(content=json.dumps(data), media_type="application/json")
        except Exception:
            return Response(
                content=json.dumps({"checkout_session_id": session_id, "status": "unknown"}),
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
        shipping_address: dict = body.get("shipping_address") or {}
        billing_address: dict = body.get("billing_address") or shipping_address

        if not hasattr(backend, "_b2b_request") or not hasattr(backend, "_ensure_webstore_id"):
            return Response(
                status_code=501,
                content=json.dumps({"error": "Backend does not support direct B2B order placement."}),
                media_type="application/json",
            )

        try:
            buyer_uid, account_id, eff_params, buyer_auth_kwargs, req_params = (
                await _resolve_buyer_params(body)
            )
            webstore_id = await backend._ensure_webstore_id()

            # ── 1. Find or create an Active cart ────────────────────────────────
            cart_id, currency = await _get_or_create_cart(
                backend, webstore_id, account_id, buyer_auth_kwargs, req_params
            )
            if not cart_id:
                return Response(status_code=500,
                    content=json.dumps({"error": "Could not locate or create a B2B cart."}),
                    media_type="application/json")

            # ── 2. Add items to the cart ─────────────────────────────────────────
            placed_items, first_item_error = await _add_items_to_cart(
                backend, webstore_id, cart_id, line_items, buyer_auth_kwargs, req_params, currency
            )
            if not placed_items:
                error_msg = "No items could be added to the cart."
                if first_item_error:
                    error_msg += f" Detail: {first_item_error}"
                return Response(status_code=500,
                    content=json.dumps({"error": error_msg}), media_type="application/json")

            # ── 3. Initiate B2B checkout session ─────────────────────────────────
            po_number = body.get("po_number", "")
            checkout_body: dict = {"cartReference": {"id": cart_id}}
            if not buyer_auth_kwargs and account_id:
                checkout_body["effectiveAccountId"] = account_id
            checkout_resp = await backend._b2b_request(
                "POST", f"/commerce/webstores/{webstore_id}/checkouts",
                **buyer_auth_kwargs, json=checkout_body,
            )
            checkout_id: str = (
                checkout_resp.get("checkoutId")
                or checkout_resp.get("cartId")
                or cart_id
            )
            dg_records = checkout_resp.get("deliveryGroups", {}).get("records", [])
            delivery_group_id = dg_records[0].get("id", "") if dg_records else ""

            # ── 4. Set payment details, shipping and billing address ──────────────
            patch_body: dict[str, Any] = {}
            if po_number:
                patch_body["poNumber"] = po_number
            if shipping_address and delivery_group_id:
                patch_body["deliveryGroups"] = {
                    "records": [{"id": delivery_group_id, "deliveryAddress": shipping_address}]
                }
            if billing_address:
                patch_body["paymentMethod"] = {"billingAddress": billing_address}
            if patch_body:
                try:
                    await backend._b2b_request(
                        "PATCH", f"/commerce/webstores/{webstore_id}/checkouts/{checkout_id}",
                        **buyer_auth_kwargs, params=req_params, json=patch_body,
                    )
                except Exception as patch_exc:
                    _log.warning("Checkout PATCH failed (continuing): %s", patch_exc)

            # ── 5. Place the order ────────────────────────────────────────────────
            place_resp = await backend._b2b_request(
                "POST",
                f"/commerce/webstores/{webstore_id}/checkouts/{checkout_id}/actions/place-order",
                **buyer_auth_kwargs, params=req_params, json={},
            )
            sf_order_id: str = (
                place_resp.get("orderId") or place_resp.get("orderSummaryId")
                or place_resp.get("id") or ""
            )
            order_number: str = (
                place_resp.get("orderNumber") or place_resp.get("orderReferenceNumber")
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
                "shipping_address": shipping_address or None,
            }
            return Response(content=json.dumps(payload), status_code=201, media_type="application/json")

        except ValueError as exc:
            return Response(status_code=400,
                content=json.dumps({"error": str(exc)}), media_type="application/json")
        except Exception as exc:
            _log.exception("ucp_place_b2b_order failed")
            return Response(
                status_code=500,
                content=json.dumps({"error": str(exc)}),
                media_type="application/json",
            )

    return router
