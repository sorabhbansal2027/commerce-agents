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
_DEFAULT_MODEL = "gemini-2.5-flash"
_model: str | None = None  # resolved lazily from env at first use


def _get_model() -> str:
    global _model
    if _model is None:
        _model = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
    return _model


def set_merchandising_backend(backend: Any) -> None:
    global _backend, _model
    _backend = backend
    _model = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)


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
        model=_get_model(),
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


# ── Google Knowledge Graph enrichment ────────────────────────────────────────


def _lookup_google_kg_sync(query: str, limit: int, api_key: str) -> list[dict]:
    """Google Knowledge Graph Search API — requires GOOGLE_KG_API_KEY (separate from GEMINI_API_KEY)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({"query": query, "key": api_key, "limit": limit})
    url = f"https://kgsearch.googleapis.com/v1/entities:search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "commerce-agents/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for item in data.get("itemListElement", []):
            r = item.get("result", {})
            dd = r.get("detailedDescription", {})
            schema_types = [t.replace("http://schema.org/", "") for t in r.get("@type", []) if t != "Thing"]
            results.append({
                "name": r.get("name", ""),
                "types": schema_types,
                "description": r.get("description", ""),
                "url": dd.get("url", ""),
                "article_body": (dd.get("articleBody") or "")[:200],
                "score": item.get("resultScore", 0),
                "entity_id": r.get("@id", ""),   # e.g. "kg:/m/0d9jr"
                "provider": "google_knowledge_graph",
            })
        return results
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:300]
        except Exception:
            pass
        log.warning("Google KG lookup failed for %r: HTTP %s — %s", query, exc.code, body)
        return []
    except Exception as exc:
        log.warning("Google KG lookup failed for %r: %s", query, exc)
        return []


def _lookup_wikidata_sync(query: str, limit: int) -> list[dict]:
    """Wikidata entity search — no API key required, open knowledge graph."""
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": limit,
        "type": "item",
    })
    url = f"https://www.wikidata.org/w/api.php?{params}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "commerce-agents/1.0 (product ontology enrichment)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for item in data.get("search", []):
            label = item.get("label", "")
            wiki_url = (
                f"https://en.wikipedia.org/wiki/{urllib.parse.quote(label.replace(' ', '_'))}"
                if label else ""
            )
            results.append({
                "name": label,
                "types": [],
                "description": item.get("description", ""),
                "url": wiki_url,
                "article_body": item.get("description", ""),
                "score": max(0, 1000 - item.get("index", 0) * 100),
                "entity_id": item.get("id", ""),   # Wikidata Q-ID e.g. "Q133485"
                "provider": "wikidata",
            })
        return results
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:300]
        except Exception:
            pass
        log.warning("Wikidata lookup failed for %r: HTTP %s — %s", query, exc.code, body)
        return []
    except Exception as exc:
        log.warning("Wikidata lookup failed for %r: %s", query, exc)
        return []


_STOP_WORDS = {"and", "or", "with", "the", "a", "an", "of", "in", "for"}

# Multi-word queries (e.g. "mahogany teakwood") resolve to 0 Wikidata entities
# because Wikidata indexes individual concepts, not fragrance blends. Split on
# whitespace and look up each term that isn't a stop word, then deduplicate by
# entity_id so a term that appears in two queries isn't returned twice.
def _lookup_kg_entities_sync(query: str, limit: int = 3) -> list[dict]:
    """Dispatch to Google KG (if GOOGLE_KG_API_KEY is set) or fall back to Wikidata.

    Multi-word compound queries (fragrance blends, two-ingredient combos) are
    split into individual terms; each term is looked up separately and results
    are deduplicated by entity_id. Single-entity two-word phrases like
    "shea butter" or "cherry blossom" resolve directly without splitting.
    """
    kg_key = os.environ.get("GOOGLE_KG_API_KEY")
    lookup = (
        lambda q, n: _lookup_google_kg_sync(q, n, kg_key)
        if kg_key
        else _lookup_wikidata_sync(q, n)
    )

    # Try the full query first.
    results = lookup(query, limit)
    if results:
        return results

    # Full query returned nothing — it's likely a blend ("mahogany teakwood").
    # Split into individual words, drop stop words, look up each.
    terms = [w for w in query.lower().split() if w not in _STOP_WORDS and len(w) > 2]
    if len(terms) <= 1:
        return results  # nothing more to try

    seen: set[str] = set()
    combined: list[dict] = []
    per_term = max(1, limit // len(terms))
    for term in terms:
        for entity in lookup(term, per_term):
            eid = entity.get("entity_id") or entity.get("name", "")
            if eid and eid not in seen:
                seen.add(eid)
                combined.append(entity)
        if len(combined) >= limit:
            break
    return combined[:limit]


async def _lookup_kg_entities(query: str, limit: int = 3) -> list[dict]:
    """Async wrapper: runs the KG HTTP call in a thread-pool executor."""
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(None, _lookup_kg_entities_sync, query, limit)


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
                {"long_description": enriched, "short_description": bullets},
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

    # Enrich semantic_attributes with Knowledge Graph entity lookup.
    # Query the product title + up to 2 key scent/ingredient attributes (capped at 3 KG calls).
    kg_queries: list[str] = [title]
    for attr_key in ("scent_notes", "key_ingredient", "fragrance_family"):
        val = (product.get("attributes") or {}).get(attr_key, "")
        for note in str(val).split(",")[:2]:
            note = note.strip()
            if note and note not in kg_queries:
                kg_queries.append(note)

    kg_attrs: list[dict] = []
    for q in kg_queries[:3]:
        entities = await _lookup_kg_entities(q, limit=1)
        if not entities:
            continue
        e = entities[0]
        key_slug = q.lower().replace(" ", "_")[:40]
        if e.get("name"):
            kg_attrs.append({
                "key": f"kg_entity:{key_slug}",
                "value": e["name"],
                "confidence": 0.9,
                "source": "knowledge_graph",
            })
        if e.get("description"):
            kg_attrs.append({
                "key": f"kg_description:{key_slug}",
                "value": e["description"][:120],
                "confidence": 1.0,
                "source": "knowledge_graph",
            })
        if e.get("url"):
            kg_attrs.append({
                "key": f"kg_url:{key_slug}",
                "value": e["url"],
                "confidence": 1.0,
                "source": "knowledge_graph",
            })
        if e.get("entity_id"):
            kg_attrs.append({
                "key": f"kg_entity_id:{key_slug}",
                "value": e["entity_id"],   # Wikidata Q-ID, e.g. "Q133485"
                "confidence": 1.0,
                "source": "knowledge_graph",
            })

    existing = result_data.get("semantic_attributes") or []
    result_data["semantic_attributes"] = existing + kg_attrs
    return result_data


async def lookup_product_entities(query: str, limit: int = 5) -> dict:
    """Look up real-world semantic entities for a product name, ingredient, or fragrance note.

    Uses Wikidata entity search (no API key required) or Google Knowledge Graph
    if GOOGLE_KG_API_KEY is set. Returns typed entity results with descriptions
    and Wikipedia URLs to ground copy claims in real-world facts.

    Args:
        query: Product name, ingredient, or fragrance note to look up
               (e.g. "mahogany teakwood", "eucalyptus", "shea butter").
        limit: Number of entities to return (1-10).
    """
    import os

    provider = "google_knowledge_graph" if os.environ.get("GOOGLE_KG_API_KEY") else "wikidata"
    entities = await _lookup_kg_entities(query, limit=min(max(1, limit), 10))
    return {"query": query, "source": provider, "entities": entities}


async def bulk_enrich_catalog(query: str = "", limit: int = 50) -> dict:
    """Enrich multiple products concurrently. Fetches up to limit products then enriches
    them in parallel (max 10 concurrent Gemini calls to stay within rate limits).

    Returns {"results": [MerchandisingResult, ...], "total": int, "failed": int}.
    """
    import asyncio

    from gemini.ontology.bbw_products import BBW_PRODUCTS

    limit = max(1, limit)

    # Gather candidates: BBW fixtures first (no network), then SFCC
    candidates: list[dict] = []
    candidates.extend(BBW_PRODUCTS[:limit])

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
    pids = [p.get("id") or p.get("listing_id", "") for p in candidates]
    pids = [pid for pid in pids if pid]

    # Enrich concurrently in batches of 10 to respect Gemini rate limits
    _CONCURRENCY = 10
    results, failed = [], 0
    for batch_start in range(0, len(pids), _CONCURRENCY):
        batch = pids[batch_start : batch_start + _CONCURRENCY]

        async def _enrich_one(pid: str) -> dict | None:
            try:
                return await enrich_product(pid)
            except Exception as exc:
                log.warning("bulk_enrich: enrich failed for %s: %s", pid, exc)
                return None

        batch_results = await asyncio.gather(*[_enrich_one(pid) for pid in batch])
        for r in batch_results:
            if r is not None:
                results.append(r)
            else:
                failed += 1

    return {"results": results, "total": len(results), "failed": failed}


# ── SPARQL / Wikidata query engine ────────────────────────────────────────────

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# Pre-built SPARQL queries for each category preset in the KG Explorer.
# Uses Wikidata property paths (P279* = subclass-of chain) and the
# wikibase:label service to return human-readable labels in English.
SPARQL_PRESETS: dict[str, dict[str, str]] = {
    "botanical": {
        "label": "🌿 Botanical extracts",
        "query": (
            "# Plant genera in the Lamiaceae family — lavender, mint, rosemary, sage, basil\n"
            "# P105=taxon rank, Q34740=genus, P171=parent taxon, Q33759=Lamiaceae\n"
            "SELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  ?item wdt:P105 wd:Q34740 ;\n"
            "        wdt:P171+ wd:Q33759 .\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} LIMIT 12"
        ),
    },
    "floral": {
        "label": "🌸 Floral ingredients",
        "query": (
            "# Flowering plant genera used in perfumery — rosa, jasmine, ylang-ylang…\n"
            "# Uses Wikidata entity search federation service\n"
            "SELECT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  SERVICE wikibase:mwapi {\n"
            "    bd:serviceParam wikibase:api \"EntitySearch\" ;\n"
            "                    wikibase:endpoint \"www.wikidata.org\" ;\n"
            "                    mwapi:search \"flower fragrance perfume\" ;\n"
            "                    mwapi:language \"en\" .\n"
            "    ?item wikibase:apiOutputItem mwapi:item .\n"
            "    ?ord wikibase:apiOrdinal true .\n"
            "  }\n"
            "  ?item wdt:P31/wdt:P279* wd:Q16521 .  # must be a taxon\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} ORDER BY ?ord LIMIT 12"
        ),
    },
    "wood": {
        "label": "🪵 Wood materials",
        "query": (
            "# Aromatic wood genera — sandalwood, cedar, pine, rosewood…\n"
            "# P105=taxon rank, Q34740=genus, P279=subclass of, Q60649=woody plant\n"
            "SELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  SERVICE wikibase:mwapi {\n"
            "    bd:serviceParam wikibase:api \"EntitySearch\" ;\n"
            "                    wikibase:endpoint \"www.wikidata.org\" ;\n"
            "                    mwapi:search \"aromatic wood tree genus\" ;\n"
            "                    mwapi:language \"en\" .\n"
            "    ?item wikibase:apiOutputItem mwapi:item .\n"
            "    ?ord wikibase:apiOrdinal true .\n"
            "  }\n"
            "  ?item wdt:P31/wdt:P279* wd:Q16521 .  # must be a taxon\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} ORDER BY ?ord LIMIT 12"
        ),
    },
    "citrus": {
        "label": "🍋 Citrus compounds",
        "query": (
            "# Monoterpenes and sesquiterpenes — limonene, linalool, citronellol…\n"
            "# P279=subclass of, Q131524=terpene (organic compound class)\n"
            "SELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  ?item wdt:P279* wd:Q131524 .\n"
            "  ?item wdt:P18 [] .\n"
            "  FILTER NOT EXISTS { ?item wdt:P31 wd:Q4167410 }\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} LIMIT 12"
        ),
    },
    "grasses": {
        "label": "🌾 Aromatic grasses",
        "query": (
            "# Grass genera in the Poaceae family — vetiver, lemongrass, citronella…\n"
            "# P105=taxon rank, Q34740=genus, P171=parent taxon, Q46078=Poaceae\n"
            "SELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  ?item wdt:P105 wd:Q34740 ;\n"
            "        wdt:P171+ wd:Q46078 .\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} LIMIT 12"
        ),
    },
    "skin": {
        "label": "🧴 Skin care actives",
        "query": (
            "# Fatty acids used in cosmetics and skin care — oleic, stearic, linoleic…\n"
            "# P279=subclass of, Q61476=fatty acid\n"
            "SELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  ?item wdt:P279* wd:Q61476 .\n"
            "  ?item wdt:P18 [] .\n"
            "  FILTER NOT EXISTS { ?item wdt:P31 wd:Q4167410 }\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} LIMIT 12"
        ),
    },
    "wax": {
        "label": "🕯️ Wax & carriers",
        "query": (
            "# Wax types used in candles, lip balm, and cosmetic bases\n"
            "# P279=subclass of, Q124695=wax\n"
            "SELECT DISTINCT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  ?item wdt:P279* wd:Q124695 .\n"
            "  FILTER NOT EXISTS { ?item wdt:P31 wd:Q4167410 }\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} LIMIT 12"
        ),
    },
    "resinous": {
        "label": "🌲 Resinous notes",
        "query": (
            "# Plant resins — frankincense, myrrh, benzoin, labdanum, elemi…\n"
            "# Uses Wikidata entity search for resin-related items\n"
            "SELECT ?item ?itemLabel ?itemDescription WHERE {\n"
            "  SERVICE wikibase:mwapi {\n"
            "    bd:serviceParam wikibase:api \"EntitySearch\" ;\n"
            "                    wikibase:endpoint \"www.wikidata.org\" ;\n"
            "                    mwapi:search \"plant resin incense perfume\" ;\n"
            "                    mwapi:language \"en\" .\n"
            "    ?item wikibase:apiOutputItem mwapi:item .\n"
            "    ?ord wikibase:apiOrdinal true .\n"
            "  }\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\" }\n"
            "} ORDER BY ?ord LIMIT 12"
        ),
    },
}


def _sparql_query_sync(query: str) -> dict:
    """POST a SPARQL query to the Wikidata endpoint; return {columns, rows, elapsed_ms}."""
    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    t0 = time.monotonic()
    req = urllib.request.Request(
        WIKIDATA_SPARQL_URL,
        data=urllib.parse.urlencode({"query": query}).encode(),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "commerce-agents/1.0 (product-ontology-demo)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:400]
        except Exception:
            pass
        raise RuntimeError(f"SPARQL HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"SPARQL request failed: {exc}") from exc

    cols = data["head"]["vars"]
    rows = []
    for binding in data["results"]["bindings"]:
        row: dict[str, str] = {}
        for col in cols:
            if col in binding:
                val = binding[col]["value"]
                # Shorten Wikidata entity URIs to bare Q-IDs
                if val.startswith("http://www.wikidata.org/entity/"):
                    val = val[len("http://www.wikidata.org/entity/"):]
                row[col] = val
            else:
                row[col] = ""
        rows.append(row)

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    return {"columns": cols, "rows": rows, "elapsed_ms": elapsed_ms}


async def sparql_kg_query(query: str) -> dict:
    """Async wrapper around _sparql_query_sync."""
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(None, _sparql_query_sync, query)
