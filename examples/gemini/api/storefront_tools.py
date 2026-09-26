"""SFCC tool functions for the Gemini ADK storefront (shopping) agent.

Each function is a plain Python callable that ADK converts into a Gemini
FunctionDeclaration. ``_sfcc`` is set once at startup by storefront_main.py.
Cart state lives in ``_carts``, keyed by session_id.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_sfcc: Any = None      # SFCCBusinessManagerBackend instance
_carts: dict[str, list[dict[str, Any]]] = {}
_current_session_id: str = "default"


def set_sfcc(sfcc: Any) -> None:
    global _sfcc
    _sfcc = sfcc


def _cart(session_id: str) -> list[dict[str, Any]]:
    return _carts.setdefault(session_id, [])


def _cart_payload(session_id: str) -> dict[str, Any]:
    items = _cart(session_id)
    subtotal = round(sum(i["price"] * i["quantity"] for i in items), 2)
    item_count = sum(i["quantity"] for i in items)
    return {
        "items": items,
        "item_count": item_count,
        "subtotal": subtotal,
        "currency": getattr(_sfcc, "_currency", "USD") if _sfcc else "USD",
    }


def _normalize_product(h: dict[str, Any]) -> dict[str, Any]:
    """Normalise an SFCC product_search hit into a shopping_agent Product shape."""
    raw_name = h.get("name", "")
    title = raw_name.get("default", "") if isinstance(raw_name, dict) else raw_name
    prices = h.get("prices", {})
    price = float(next(iter(prices.values()), 0.0) or 0.0)
    raw_desc = h.get("short_description", h.get("long_description", ""))
    short_desc = raw_desc.get("default", "") if isinstance(raw_desc, dict) else raw_desc
    image = None
    image_groups = h.get("image_groups") or []
    for g in image_groups:
        imgs = g.get("images") or []
        if imgs:
            image = imgs[0].get("dis_base_link") or imgs[0].get("link")
            if image:
                break
    return {
        "product_id": h.get("id", ""),
        "title": title,
        "price": price,
        "currency": getattr(_sfcc, "_currency", "USD") if _sfcc else "USD",
        "category": h.get("primary_category_id"),
        "short_description": short_desc or None,
        "image_url": image,
        "in_stock": h.get("online_flag", {}).get("default", True) if isinstance(h.get("online_flag"), dict) else True,
        "labels": [],
        "attributes": {},
    }


# ── Tools ─────────────────────────────────────────────────────────────────────


async def search_products(query: str = "", category: str = "", limit: int = 12) -> dict:
    """Search the DreamHaus product catalog.

    Args:
        query: Keywords to search for; leave empty to browse all products.
        category: Optional category filter (e.g. "jewelry", "rings").
        limit: Maximum number of products to return (1–24).
    """
    if _sfcc is None:
        return {"products": [], "error": "backend not initialised"}
    try:
        base_q: dict[str, Any] = (
            {"match_all_query": {}}
            if not query
            else {"text_query": {"fields": ["id", "name", "short_description"], "search_phrase": query}}
        )
        body: dict[str, Any] = {"query": base_q, "select": "(**)", "count": max(1, min(limit, 24))}
        if category:
            body["query"] = {
                "filtered_query": {
                    "query": body["query"],
                    "filter": {"term_filter": {
                        "field": "primary_category_id",
                        "operator": "is",
                        "values": [category],
                    }},
                }
            }
        data = await _sfcc._request("POST", "product_search", json=body)
        hits = (data or {}).get("hits", [])
        return {"products": [_normalize_product(h) for h in hits]}
    except Exception as exc:
        log.warning("search_products failed: %s", exc)
        return {"products": [], "error": str(exc)}


async def get_product(product_id: str) -> dict:
    """Get full details for a single product by ID.

    Args:
        product_id: The product's ID from a search result.
    """
    if _sfcc is None:
        return {"error": "backend not initialised"}
    try:
        data = await _sfcc._request("GET", f"products/{product_id}?select=(**)", site=True)
        if not data:
            return {"error": f"Product {product_id!r} not found"}
        return _normalize_product(data)
    except Exception as exc:
        log.warning("get_product %s failed: %s", product_id, exc)
        return {"error": str(exc)}


def add_to_cart(session_id: str, product_id: str, title: str, price: float, quantity: int = 1, image_url: str = "") -> dict:
    """Add a product to the shopper's cart.

    Args:
        session_id: The current session ID.
        product_id: Product to add.
        title: Product title shown in the cart.
        price: Unit price.
        quantity: Number of units to add (default 1).
        image_url: Optional product image URL.
    """
    items = _cart(session_id)
    for item in items:
        if item["product_id"] == product_id:
            item["quantity"] += max(1, quantity)
            return {"ok": True, "cart": _cart_payload(session_id)}
    items.append({
        "product_id": product_id,
        "title": title,
        "price": float(price),
        "quantity": max(1, quantity),
        "image_url": image_url or None,
    })
    return {"ok": True, "cart": _cart_payload(session_id)}


def remove_from_cart(session_id: str, product_id: str) -> dict:
    """Remove a product from the shopper's cart.

    Args:
        session_id: The current session ID.
        product_id: Product to remove.
    """
    items = _cart(session_id)
    _carts[session_id] = [i for i in items if i["product_id"] != product_id]
    return {"ok": True, "cart": _cart_payload(session_id)}


def update_cart_quantity(session_id: str, product_id: str, quantity: int) -> dict:
    """Update the quantity of a product in the cart.

    Args:
        session_id: The current session ID.
        product_id: Product to update.
        quantity: New quantity (0 removes the item).
    """
    if quantity <= 0:
        return remove_from_cart(session_id, product_id)
    items = _cart(session_id)
    for item in items:
        if item["product_id"] == product_id:
            item["quantity"] = quantity
            return {"ok": True, "cart": _cart_payload(session_id)}
    return {"ok": False, "error": f"{product_id!r} not in cart"}


def get_cart(session_id: str) -> dict:
    """Return the current cart contents.

    Args:
        session_id: The current session ID.
    """
    return _cart_payload(session_id)


ALL_STOREFRONT_TOOLS = [
    search_products,
    get_product,
    add_to_cart,
    remove_from_cart,
    update_cart_quantity,
    get_cart,
]
