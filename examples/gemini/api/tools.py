"""SFCC tool functions for the Gemini ADK merchant agent.

Each function is a plain Python callable that ADK converts into a Gemini
FunctionDeclaration. The global ``_backend`` is set once at startup by main.py.
A minimal MerchantSessionContext (merchant_id only) is passed to the backend's
async methods; ADK calls tools in the event loop so ``asyncio.run`` is not needed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_backend: Any = None


def _session() -> Any:
    """Minimal session context satisfying MerchantSessionContext fields."""
    from merchant_agent import MerchantSessionContext

    return MerchantSessionContext(
        merchant_id="gemini-merchant",
        session_id="adk-session",
        operator="gemini-operator",
    )


def set_backend(backend: Any) -> None:
    global _backend
    _backend = backend


# ── Read tools ────────────────────────────────────────────────────────────────


async def get_business_snapshot(period: str = "30d") -> dict:
    """Return headline business figures: sales, orders, and alert counts for a period.

    Args:
        period: Reporting window such as 7d, 30d, or an ISO date range.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        snap = await _backend.get_business_snapshot(_session(), period)
        return snap.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        log.warning("get_business_snapshot failed: %s", exc)
        return {"sales": 0.0, "orders": 0, "period": period, "note": str(exc)}


async def get_inventory_alerts() -> dict:
    """Return products that are low in stock or out of stock and need restocking."""
    if _backend is None:
        return {"alerts": []}
    try:
        alerts = await _backend.get_inventory_alerts(_session())
        return {"alerts": [a.model_dump(mode="json", exclude_none=True) for a in alerts]}
    except Exception as exc:
        log.warning("get_inventory_alerts failed: %s", exc)
        return {"alerts": [], "error": str(exc)}


async def get_order_issues() -> dict:
    """Return open order exceptions such as failed or held orders awaiting attention."""
    if _backend is None:
        return {"issues": []}
    try:
        issues = await _backend.get_order_issues(_session())
        return {"issues": [i.model_dump(mode="json", exclude_none=True) for i in issues]}
    except Exception as exc:
        log.warning("get_order_issues failed: %s", exc)
        return {"issues": [], "error": str(exc)}


async def search_listings(query: str, limit: int = 8, category: str = "") -> dict:
    """Search the product catalog and return matching listings with id, title, price, and status.

    Args:
        query: Text to match against product names and IDs. Use empty string to browse all.
        limit: Maximum number of results to return (1-50).
        category: Optional catalog category to filter by.
    """
    if _backend is None:
        return {"listings": []}
    try:
        from merchant_agent import ListingFilters

        filters = ListingFilters(category=category or None) if category else None
        results = await _backend.search_listings(_session(), query, filters, min(limit, 50))
        return {"listings": [r.model_dump(mode="json", exclude_none=True) for r in results]}
    except Exception as exc:
        log.warning("search_listings failed: %s", exc)
        return {"listings": [], "error": str(exc)}


async def get_listing(listing_id: str) -> dict:
    """Return the full record for one product: content, attributes, price, and stock level.

    Args:
        listing_id: The product ID returned by search_listings.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        result = await _backend.get_listing(_session(), listing_id)
        if result is None:
            return {"error": f"listing {listing_id} not found"}
        return result.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        log.warning("get_listing failed: %s", exc)
        return {"error": str(exc)}


async def get_pending_changes() -> dict:
    """Return staged changes that have not yet been applied or discarded."""
    if _backend is None:
        return {"changes": []}
    try:
        changes = await _backend.get_pending_changes(_session())
        return {"changes": [c.model_dump(mode="json", exclude_none=True) for c in changes]}
    except Exception as exc:
        log.warning("get_pending_changes failed: %s", exc)
        return {"changes": [], "error": str(exc)}


async def get_pending_quote_approvals() -> dict:
    """Return quotes submitted by buyers that are waiting for merchant approval."""
    if _backend is None:
        return {"quotes": []}
    try:
        quotes = await _backend.get_pending_quote_approvals(_session())
        return {"quotes": [q.model_dump(mode="json", exclude_none=True) for q in quotes]}
    except Exception as exc:
        log.warning("get_pending_quote_approvals failed: %s", exc)
        return {"quotes": [], "error": str(exc)}


# ── Write tools ───────────────────────────────────────────────────────────────


async def stage_listing_update(listing_id: str, fields: dict, note: str = "") -> dict:
    """Stage content or attribute edits for a listing (title, description, attributes).

    Args:
        listing_id: The product ID to update.
        fields: Dictionary of field names to new values, e.g. {"title": "New Name"}.
        note: One sentence explaining why the edit is right.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        change = await _backend.stage_listing_update(_session(), listing_id, fields, note or None)
        return change.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        log.warning("stage_listing_update failed: %s", exc)
        return {"error": str(exc)}


async def stage_inventory_action(
    listing_id: str, action: str, quantity: int = 0, note: str = ""
) -> dict:
    """Stage a restock, pause, or activate action for a listing.

    Args:
        listing_id: The product ID to act on.
        action: One of restock, pause, or activate.
        quantity: Units to add for a restock action.
        note: One sentence on the reasoning.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        items = [{"listing_id": listing_id, "action": action, "quantity": quantity}]
        change = await _backend.stage_inventory_action(_session(), items, note or None)
        return change.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        log.warning("stage_inventory_action failed: %s", exc)
        return {"error": str(exc)}


async def apply_change(change_id: str) -> dict:
    """Apply a staged change the operator has approved. This is the only call that modifies live state.

    Args:
        change_id: The change_id from get_pending_changes or a stage_ tool result.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        result = await _backend.apply_change(_session(), change_id)
        return result.model_dump(mode="json", exclude_none=True) if result else {"applied": change_id}
    except Exception as exc:
        log.warning("apply_change failed: %s", exc)
        return {"error": str(exc)}


async def discard_change(change_id: str) -> dict:
    """Discard a staged change the operator decided against.

    Args:
        change_id: The change_id to discard.
    """
    if _backend is None:
        return {"error": "backend not initialised"}
    try:
        await _backend.discard_change(_session(), change_id)
        return {"discarded": change_id}
    except Exception as exc:
        log.warning("discard_change failed: %s", exc)
        return {"error": str(exc)}


# ── All tools exported for the ADK agent ─────────────────────────────────────

ALL_TOOLS = [
    get_business_snapshot,
    get_inventory_alerts,
    get_order_issues,
    search_listings,
    get_listing,
    get_pending_changes,
    get_pending_quote_approvals,
    stage_listing_update,
    stage_inventory_action,
    apply_change,
    discard_change,
]
