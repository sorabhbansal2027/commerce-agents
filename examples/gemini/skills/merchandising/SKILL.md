# Skill: Merchandising

Handles all product content enrichment: descriptions, SEO copy, geo variants, ontology classification, and Knowledge Graph grounding.

## When this skill applies

- Operator asks to enrich, improve, or rewrite a product description
- Operator asks for SEO titles, meta descriptions, or URL slugs
- Operator asks to adapt copy for a specific region or market
- Operator asks to classify a product by category, occasion, or style
- Operator asks about product ingredients, materials, or fragrance notes

## Flow — full enrichment

1. Call `get_listing` (or `search_listings` if no ID given) to read the product.
2. Call `classify_product_ontology` to establish category, occasions, and style tags.
3. For ingredients or materials, call `lookup_product_entities` to ground claims in Knowledge Graph facts before writing copy.
4. Call `enrich_product` to generate the vivid B2C description and benefit bullets.
5. Call `generate_seo_content` if the operator wants search-optimised copy.
6. Call `generate_geo_content` for region-specific variants.
7. Call `stage_listing_update` with the enriched fields and a note citing the source data.
8. Show a before/after comparison and ask for approval.

## Rules

- Never invent specifications, dimensions, or ingredient claims — ground every fact in `get_listing` attributes or `lookup_product_entities` results.
- Do not enrich a listing without reading it first.
- Benefit bullets must be concrete (specific burn time, specific size) not vague ("amazing quality").
- SEO titles must be ≤60 characters; meta descriptions ≤155 characters.
- For geo variants, state the region and seasonal context — do not reuse the base description verbatim.
- Enriched content is staged, not applied. Always show the staged result and ask for approval.
