"""SFCC tool functions for the Gemini ADK merchant agent.

Each function is a plain Python callable that ADK converts into a Gemini
FunctionDeclaration. The global ``_backend`` is set once at startup by main.py.
A minimal MerchantSessionContext (merchant_id only) is passed to the backend's
async methods; ADK calls tools in the event loop so ``asyncio.run`` is not needed.
"""

from __future__ import annotations

import logging
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


async def approve_change(change_id: str) -> dict:
    """Record explicit operator approval for a staged change, enabling apply_change to proceed.

    Call this ONLY after the operator has explicitly said they approve a specific change
    (e.g. "approve", "apply change <id>", "yes, apply that"). The approval gate on
    apply_change blocks execution until this is called.

    Args:
        change_id: The change_id returned by a stage_ tool or get_pending_changes.
    """
    return {"approved": change_id, "status": "approval_recorded"}


async def run_analysis(brief: str, metrics: dict | None = None) -> dict:
    """Compute a derived metric or answer a specific analytical question using Gemini.

    Use for questions that require computation across multiple figures — segment drivers,
    period comparisons, correlation between metrics, or "why did X change" questions.
    Do NOT use for plain lookups that a snapshot already answers.

    Args:
        brief: One or two sentences describing exactly what to compute and why.
               Include the specific metrics and period in scope.
        metrics: Optional dict of raw figures to reason over (from get_business_snapshot
                 or other tools). Pass what you have; Gemini will work with it.
    """
    try:
        from google import genai
        from google.genai import types as gtypes

        import os
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

        metrics_str = ""
        if metrics:
            import json as _json
            metrics_str = f"\n\nAvailable metrics:\n{_json.dumps(metrics, indent=2)}"

        prompt = (
            f"You are a commerce analyst. Answer this analytical question concisely, "
            f"showing your reasoning. Return a JSON object with fields: "
            f"'answer' (string, 1-3 sentences), 'computation' (string, the steps taken), "
            f"'confidence' ('high'|'medium'|'low'), 'caveat' (string or null).\n\n"
            f"Question: {brief}{metrics_str}"
        )

        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        import json as _json
        return _json.loads(response.text)
    except Exception as exc:
        log.warning("run_analysis failed: %s", exc)
        return {"answer": f"Analysis unavailable: {exc}", "confidence": "low", "caveat": str(exc)}


async def apply_change(change_id: str) -> dict:
    """Apply a staged change the operator has approved. This is the only call that modifies live state.

    The approval gate blocks this tool until approve_change is called for this change_id.

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


# ── Merchandising tools ───────────────────────────────────────────────────────


async def enrich_product(listing_id: str, aspects: str = "all") -> dict:
    """Generate a vivid B2C product description, benefit bullets, and semantic attributes for a listing.

    Use this when asked to enrich, improve, or generate content for a product.

    Args:
        listing_id: The product ID to enrich (supports BBW-* fixture IDs and SFCC product IDs).
        aspects: Aspects to enrich — use "all" to enrich description and benefits together.
    """
    from .merchandising import enrich_product as _enrich

    return await _enrich(listing_id, aspects=aspects)


async def generate_seo_content(listing_id: str, market: str = "US", brand: str = "DreamHaus") -> dict:
    """Generate keyword-optimised SEO title, meta description, H1, and URL slug for a product.

    Use this when asked to generate SEO content, optimise for search, or improve discoverability.

    Args:
        listing_id: The product ID (supports BBW-* fixture IDs and SFCC product IDs).
        market: Target market, e.g. US, UK, CA.
        brand: Brand name to include at the end of the SEO title.
    """
    from .merchandising import generate_seo_content as _seo

    return await _seo(listing_id, market=market, brand=brand)


async def generate_geo_content(listing_id: str, regions: str = "US-northeast,UK") -> dict:
    """Generate region-specific product description variants for different geographic markets.

    Use this when asked to localise content, adapt descriptions for different regions, or
    create geo-targeted copy.

    Args:
        listing_id: The product ID (supports BBW-* fixture IDs and SFCC product IDs).
        regions: Comma-separated list from: US-northeast, US-south, US-west, UK, CA.
    """
    from .merchandising import generate_geo_content as _geo

    return await _geo(listing_id, regions=regions)


async def classify_product_ontology(listing_id: str) -> dict:
    """Classify a product into the B2C taxonomy: category, occasions, style tags, and target persona.

    Use this when asked to classify, tag, or categorise a product for collection building or navigation.
    The result includes Knowledge Graph entity IDs and Wikipedia descriptions for key ingredients.

    Args:
        listing_id: The product ID (supports BBW-* fixture IDs and SFCC product IDs).
    """
    from .merchandising import classify_product_ontology as _classify

    return await _classify(listing_id)


async def lookup_product_entities(query: str, limit: int = 5) -> dict:
    """Look up real-world semantic entities for a product name, ingredient, or fragrance note in Google Knowledge Graph.

    Use this before writing copy about specific ingredients or materials to ground claims in
    real-world facts — for example, to get the Wikipedia description of "teakwood", "eucalyptus
    spearmint", or "shea butter" before including them in enriched product descriptions.

    Args:
        query: Product name, ingredient, or fragrance note to look up
               (e.g. "mahogany teakwood", "eucalyptus spearmint", "shea butter").
        limit: Number of entities to return (1-10).
    """
    from .merchandising import lookup_product_entities as _kglookup

    return await _kglookup(query, limit=limit)


# ── All tools exported for both ADK (Path 2) and genai-raw (Path 1) ──────────

ALL_TOOLS = [
    get_business_snapshot,
    get_inventory_alerts,
    get_order_issues,
    run_analysis,
    search_listings,
    get_listing,
    get_pending_changes,
    get_pending_quote_approvals,
    stage_listing_update,
    stage_inventory_action,
    approve_change,
    apply_change,
    discard_change,
    # Merchandising tools
    enrich_product,
    generate_seo_content,
    generate_geo_content,
    classify_product_ontology,
    lookup_product_entities,
]

# Deduplicated function map used by the genai-raw path's manual dispatch loop
ALL_TOOL_FUNCTIONS: dict[str, Any] = {fn.__name__: fn for fn in dict.fromkeys(ALL_TOOLS)}


def get_tool_declarations() -> list:
    """Return deduplicated tool callables for google.genai auto-conversion to FunctionDeclarations."""
    return list(dict.fromkeys(ALL_TOOLS))
