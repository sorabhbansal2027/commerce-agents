# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Merchandising tools for the Gemini ADK merchant agent.

Each function calls Gemini directly via google.genai.Client.aio to generate
structured merchandising content (enrichment, SEO, geo variants, ontology
classification) from product data.

The module holds its own reference to the shared backend so it can fetch
product data independently of the ADK tool wrappers in tools.py.

BBW product fixtures are checked first; if not found, the SFCC backend is used.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_backend: Any = None
_model: str = "gemini-2.0-flash"


def set_merchandising_backend(backend: Any) -> None:
    global _backend, _model
    _backend = backend
    _model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def _client():
    from google import genai  # type: ignore[import]

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


def _session() -> Any:
    from merchant_agent import MerchantSessionContext

    return MerchantSessionContext(
        merchant_id="gemini-merchant",
        session_id="merch-session",
        operator="gemini-operator",
    )


async def _get_product(listing_id: str) -> dict | None:
    """Fetch product from BBW fixtures first, then SFCC backend."""
    from gemini.ontology.bbw_products import get_bbw_product

    product = get_bbw_product(listing_id)
    if product:
        return product

    if _backend is None:
        return None
    try:
        result = await _backend.get_listing(_session(), listing_id)
        if result is None:
            return None
        return result.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        log.warning("get_product failed for %s: %s", listing_id, exc)
        return None


async def _generate_json(prompt: str) -> dict | list:
    """Call Gemini and parse the JSON response."""
    from google.genai import types as gtypes  # type: ignore[import]

    client = _client()
    response = await client.aio.models.generate_content(
        model=_model,
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.4,
        ),
    )
    raw = response.text or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Gemini returned non-JSON: %s", raw[:200])
        return {}


# ── Merchandising endpoints ───────────────────────────────────────────────────


async def enrich_product(
    listing_id: str,
    aspects: str = "all",
    auto_stage: bool = False,
) -> dict:
    """Generate a vivid B2C product description, benefit bullets, and semantic attributes.

    Returns a MerchandisingResult dict. If auto_stage is True, the enriched
    description is also staged as a listing update.
    """
    from gemini.ontology.taxonomy import BENEFIT_VOCABULARY

    product = await _get_product(listing_id)
    if not product:
        return {"error": f"Product {listing_id} not found"}

    title = product.get("title", listing_id)
    description = product.get("description", "")
    category = product.get("category", "other")
    attributes = product.get("attributes", {})
    benefit_vocab = BENEFIT_VOCABULARY.get(category, BENEFIT_VOCABULARY["other"])

    prompt = f"""You are a B2C product copywriter specialising in beauty and lifestyle brands.
Generate enriched merchandising content for the following product. Return ONLY valid JSON.

Product:
- ID: {listing_id}
- Title: {title}
- Description: {description}
- Category: {category}
- Attributes: {json.dumps(attributes, indent=2)}

Approved benefit vocabulary for this category: {benefit_vocab}

Return this exact JSON structure:
{{
  "enriched_description": "2-3 sentence vivid B2C paragraph that is benefit-led, sensory, and emotionally resonant",
  "benefits_bullets": ["bullet 1 (start with action word)", "bullet 2", "bullet 3", "bullet 4"],
  "semantic_attributes": [
    {{"key": "material", "value": "...", "confidence": 0.9, "source": "product_data"}},
    {{"key": "style", "value": "...", "confidence": 0.8, "source": "ai_generated"}},
    {{"key": "occasion", "value": "...", "confidence": 0.85, "source": "ai_generated"}}
  ],
  "target_persona": "one of: self-gifter | gift-giver | home decorator | wellness seeker | everyday shopper"
}}

Rules:
- Only use facts that can be inferred from the product data above. Do not invent specifications.
- Benefit bullets must be scannable (under 10 words each).
- Use 3-5 benefit bullets.
- enriched_description must be under 200 words.
"""

    result_data = await _generate_json(prompt)
    if not isinstance(result_data, dict):
        result_data = {}

    ontology = {
        "listing_id": listing_id,
        "category": category,
        "subcategory": attributes.get("type") or attributes.get("size"),
        "style_tags": [],
        "occasions": [],
        "benefits": result_data.get("benefits_bullets", []),
        "target_persona": result_data.get("target_persona"),
        "semantic_attributes": result_data.get("semantic_attributes", []),
    }

    staged_change_id = None
    if auto_stage and _backend is not None:
        try:
            enriched = result_data.get("enriched_description", "")
            bullets = "\n".join(f"• {b}" for b in result_data.get("benefits_bullets", []))
            change = await _backend.stage_listing_update(
                _session(),
                listing_id,
                {"description": enriched, "benefits": bullets},
                "AI-generated B2C content enrichment",
            )
            staged_change_id = getattr(change, "change_id", None)
        except Exception as exc:
            log.warning("auto_stage failed: %s", exc)

    return {
        "listing_id": listing_id,
        "original_title": title,
        "enriched_description": result_data.get("enriched_description", ""),
        "benefits_bullets": result_data.get("benefits_bullets", []),
        "ontology": ontology,
        "seo": None,
        "geo_variants": [],
        "staged_change_id": staged_change_id,
    }


async def generate_seo_content(
    listing_id: str,
    market: str = "US",
    brand: str = "DreamHaus",
) -> dict:
    """Generate keyword-optimised SEO title, meta description, H1, and URL slug.

    Returns a SEOContent dict.
    """
    from gemini.ontology.taxonomy import SEO_KEYWORD_TEMPLATES

    product = await _get_product(listing_id)
    if not product:
        return {"error": f"Product {listing_id} not found"}

    title = product.get("title", listing_id)
    description = product.get("description", "")
    category = product.get("category", "other")
    attributes = product.get("attributes", {})
    keyword_templates = SEO_KEYWORD_TEMPLATES.get(category, SEO_KEYWORD_TEMPLATES.get("other", []))

    prompt = f"""You are an e-commerce SEO specialist.
Generate keyword-optimised SEO content for this product. Return ONLY valid JSON.

Product:
- ID: {listing_id}
- Title: {title}
- Description: {description}
- Category: {category}
- Attributes: {json.dumps(attributes, indent=2)}
- Target market: {market}
- Brand: {brand}

Keyword templates to draw from: {keyword_templates}

Return this exact JSON structure:
{{
  "title": "SEO title — keyword-first, under 60 characters, include brand at end after pipe",
  "meta_description": "155 chars max. Natural language. Include primary keyword near start. End with a soft CTA.",
  "h1": "H1 tag — similar to title but can be slightly longer and more descriptive",
  "keywords": ["primary keyword", "secondary 1", "secondary 2", "secondary 3", "secondary 4"],
  "slug": "url-friendly-slug-no-spaces-lowercase-hyphens"
}}

Rules:
- title MUST be under 60 characters.
- meta_description MUST be under 155 characters.
- slug must contain only lowercase letters, numbers, and hyphens.
- Primary keyword must appear in both title and meta_description.
- Do not use the brand name in keywords (it is already in the title).
"""

    result_data = await _generate_json(prompt)
    if not isinstance(result_data, dict):
        return {"error": "Gemini did not return valid SEO content"}

    return {
        "title": result_data.get("title", title),
        "meta_description": result_data.get("meta_description", ""),
        "h1": result_data.get("h1", title),
        "keywords": result_data.get("keywords", []),
        "slug": result_data.get("slug", listing_id.lower().replace(" ", "-")),
    }


async def generate_geo_content(
    listing_id: str,
    regions: str = "US-northeast,UK",
) -> dict:
    """Generate region-specific product description variants and occasion tags.

    regions: comma-separated list from US-northeast, US-south, US-west, UK, CA.
    Returns {"variants": [GeoContent, ...]}.
    """
    from gemini.ontology.taxonomy import GEO_OCCASION_MAP

    product = await _get_product(listing_id)
    if not product:
        return {"error": f"Product {listing_id} not found"}

    title = product.get("title", listing_id)
    description = product.get("description", "")
    category = product.get("category", "other")
    attributes = product.get("attributes", {})

    region_list = [r.strip() for r in regions.split(",") if r.strip()]
    region_context = {r: GEO_OCCASION_MAP.get(r, {}) for r in region_list}

    prompt = f"""You are a regional content strategist for a B2C lifestyle brand.
Generate localised product description variants for each region. Return ONLY valid JSON.

Product:
- ID: {listing_id}
- Title: {title}
- Description: {description}
- Category: {category}
- Attributes: {json.dumps(attributes, indent=2)}

Regions with seasonal occasion context:
{json.dumps(region_context, indent=2)}

Return this exact JSON structure (one entry per region):
[
  {{
    "region": "US-northeast",
    "title_variant": "optional region-specific title variant, or null",
    "description_variant": "2-3 sentences adapted for this region's culture, season, and occasions. Warm and inviting.",
    "occasion_tags": ["tag1", "tag2", "tag3"],
    "seasonal_notes": "one sentence on seasonal relevance, or null"
  }}
]

Rules:
- description_variant must be 2-3 sentences, under 150 words.
- Use vocabulary and occasions appropriate for each region (e.g. UK: 'pressie', 'cosy'; US-south: 'barbecue', 'porch').
- occasion_tags should be 2-4 short phrases from the regional context provided.
- Only generate variants for the regions listed: {region_list}.
"""

    result_data = await _generate_json(prompt)
    if not isinstance(result_data, list):
        result_data = []

    return {"variants": result_data}


async def classify_product_ontology(listing_id: str) -> dict:
    """Classify a product into the B2C ontology: category, occasions, style tags, persona.

    Returns a ProductOntology dict.
    """
    from gemini.ontology.schema import ProductCategory
    from gemini.ontology.taxonomy import CATEGORY_TAXONOMY

    product = await _get_product(listing_id)
    if not product:
        return {"error": f"Product {listing_id} not found"}

    title = product.get("title", listing_id)
    description = product.get("description", "")
    category = product.get("category", "other")
    attributes = product.get("attributes", {})

    categories = [c.value for c in ProductCategory]
    all_subcategories = CATEGORY_TAXONOMY

    prompt = f"""You are a product taxonomy specialist for a B2C lifestyle brand.
Classify this product into the brand's ontology. Return ONLY valid JSON.

Product:
- ID: {listing_id}
- Title: {title}
- Description: {description}
- Category hint: {category}
- Attributes: {json.dumps(attributes, indent=2)}

Available categories: {categories}
Subcategory options by category: {json.dumps(all_subcategories, indent=2)}

Return this exact JSON structure:
{{
  "listing_id": "{listing_id}",
  "category": "one of the available categories above",
  "subcategory": "one of the subcategory options for the chosen category, or null",
  "style_tags": ["tag1", "tag2", "tag3"],
  "occasions": ["occasion1", "occasion2", "occasion3"],
  "benefits": ["benefit1", "benefit2", "benefit3"],
  "target_persona": "one of: self-gifter | gift-giver | home decorator | wellness seeker | everyday shopper",
  "semantic_attributes": [
    {{"key": "fragrance_family", "value": "...", "confidence": 0.9, "source": "product_data"}},
    {{"key": "scent_intensity", "value": "...", "confidence": 0.7, "source": "ai_generated"}}
  ]
}}

Rules:
- style_tags: 2-4 short descriptive words (e.g. "cozy", "minimalist", "festive", "layerable").
- occasions: 2-4 use occasions (e.g. "gifting", "everyday", "holiday", "self-care").
- benefits: 2-4 product benefits drawn from what the product data supports.
- Only use facts that can be inferred from product data. Do not invent specifications.
"""

    result_data = await _generate_json(prompt)
    if not isinstance(result_data, dict):
        return {
            "listing_id": listing_id,
            "category": category,
            "subcategory": None,
            "style_tags": [],
            "occasions": [],
            "benefits": [],
            "target_persona": None,
            "semantic_attributes": [],
        }

    result_data["listing_id"] = listing_id
    return result_data


async def bulk_enrich_catalog(query: str = "", limit: int = 5) -> dict:
    """Enrich multiple products at once. Fetches up to limit products and enriches each.

    Returns {"results": [MerchandisingResult, ...], "total": int}.
    """
    from gemini.ontology.bbw_products import BBW_PRODUCTS

    limit = max(1, min(limit, 5))

    # Gather candidates: BBW fixtures + SFCC search
    candidates: list[dict] = []

    # BBW products first (no network needed)
    candidates.extend(BBW_PRODUCTS[:limit])

    # If we still have room, fetch from SFCC
    if len(candidates) < limit and _backend is not None:
        try:
            sfcc_results = await _backend.search_listings(
                _session(), query, None, limit - len(candidates)
            )
            for r in sfcc_results:
                candidates.append(r.model_dump(mode="json", exclude_none=True))
        except Exception as exc:
            log.warning("bulk_enrich: SFCC search failed: %s", exc)

    candidates = candidates[:limit]

    results = []
    for product in candidates:
        pid = product.get("id") or product.get("listing_id", "")
        if not pid:
            continue
        try:
            result = await enrich_product(pid)
            results.append(result)
        except Exception as exc:
            log.warning("bulk_enrich: enrich failed for %s: %s", pid, exc)

    return {"results": results, "total": len(results)}
