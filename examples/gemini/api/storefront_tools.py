"""Salesforce B2B Commerce tool functions for the Gemini ADK storefront agent.

Each function is a plain Python callable that ADK converts into a Gemini
FunctionDeclaration. ``_backend`` is set once at startup by storefront_main.py.
Cart state lives in ``_carts``, keyed by session_id.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_backend: Any = None   # ETSOMSBackend instance
_carts: dict[str, list[dict[str, Any]]] = {}


def set_backend(backend: Any) -> None:
    global _backend
    _backend = backend


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
        "currency": "USD",
    }


def _product_to_dict(p: Any) -> dict[str, Any]:
    """Convert a shopping_agent Product/ProductDetails to a frontend-ready dict."""
    return {
        "product_id": p.product_id,
        "title": p.title,
        "price": float(p.price),
        "currency": getattr(p, "currency", "USD") or "USD",
        "category": getattr(p, "category", None),
        "short_description": getattr(p, "short_description", None) or None,
        "image_url": getattr(p, "image_url", None),
        "in_stock": getattr(p, "in_stock", True),
        "labels": list(getattr(p, "labels", [])),
        "attributes": dict(getattr(p, "attributes", {})),
    }


def _sf_ctx() -> Any:
    """Minimal ShoppingSessionContext for catalog-only calls."""
    from shopping_agent import ShoppingSessionContext
    return ShoppingSessionContext(session_id="storefront", user_id="guest")


# ── Tools ─────────────────────────────────────────────────────────────────────


async def search_products(query: str = "", category: str = "", limit: int = 12) -> dict:
    """Search the B2B Commerce product catalog.

    Args:
        query: Keywords to search for; leave empty to browse all products.
        category: Optional category filter (product family, e.g. "Laptops").
        limit: Maximum number of products to return (1–24).
    """
    if _backend is None:
        return {"products": [], "error": "backend not initialised"}
    try:
        from shopping_agent import SearchFilters
        ctx = _sf_ctx()
        filters = SearchFilters(category=category) if category else None
        products = await _backend.search_products(
            ctx, query or "", filters, limit=max(1, min(limit, 24))
        )
        return {"products": [_product_to_dict(p) for p in products]}
    except Exception as exc:
        log.warning("search_products failed: %s", exc)
        return {"products": [], "error": str(exc)}


async def get_product(product_id: str) -> dict:
    """Get full details for a single product by ID.

    Args:
        product_id: The product's Salesforce record ID from a search result.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        ctx = _sf_ctx()
        p = await _backend.get_product_details(ctx, product_id)
        if not p:
            return {"error": f"Product {product_id!r} not found"}
        return _product_to_dict(p)
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
