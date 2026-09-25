---
name: product-merchandising
description: SEO attribute generation, geo- and locale-specific content, and structured data enrichment for product listings — covering keyword-optimized titles, search-intent descriptions, regional adaptations, and missing attribute completion from product evidence. Not needed for general copy rewrites (catalog-listings) or price changes (pricing-promotions).
---

# Product merchandising

Below, "listing" means whatever this operation sells: a product, a room type, a plan, or an event tier.

Read the record fully before proposing any change. Every attribute value you write must come from the record or from what the operator supplied in this conversation; a value that is merely plausible for the category is a fabrication — leave the field blank or ask, one line per open field.

## SEO attributes

- Fetch the record with `get_listing` and `recall_memories` for naming conventions before writing a single word.
- An SEO title carries the words a buyer types: the primary keyword phrase first, then the one or two differentiating facts the record supports (material, size, compatibility, occupancy). Drop filler that carries no search signal ("Amazing", "Best", "New").
- A meta description is one or two sentences — what the item is, one concrete differentiator, and a natural call to action. Write it to earn the click, not to restate the title. It must fit within 155 characters; count before staging.
- Identify the primary keyword phrase from the category and the attributes in the record; do not invent a phrase the record does not support. If the operator named a target keyword, place it in both the title and the meta description exactly as given.
- Fill search-relevant attributes the record omits but that the description or specs already name (color, material, compatibility, dimensions). A description that says "fits standard 65-inch panels" supplies the `compatibility` attribute; a line that says "brushed aluminum finish" supplies `material` and `color`.
- For a family listing, write one SEO title and description that covers the range ("available in S–XL", "three finishes"); each variant's `attributes` then carries its specific value.

## Geo- and locale-specific content

- Ask for the target locale or region if the operator has not named one; do not assume.
- Adapt the title and description to the region's buyer intent and vocabulary: the word a US buyer types for a sofa may differ from the UK term; a product priced for a North American market may emphasize different benefits in a Southeast Asian market. Use the operator-supplied locale name or a specific fact they gave about the market.
- Metric vs. imperial: convert dimensions and weights to the unit the region uses; show both only if the operator explicitly asks. Take the source figure from the record; compute the conversion and state the assumption once ("converting from cm to inches as stated").
- Regional regulatory attributes (energy ratings, compliance marks, safety standards) go in their own named attribute fields, not inside the description. Leave them blank if the record does not supply them and flag them as open questions.
- Stage geo content as a separate update from the default-locale content so the operator can approve each region independently. Name the locale in the staging note ("DE locale: translated title and compliance attributes").

## Structured data enrichment

- Run an enrichment pass from `search_listings` with the quality filter, then `get_listing` on each candidate; measure missing attributes against the category's expected attribute set (as the operator describes it or as `missing_attributes` in the record reports).
- Fill a missing attribute from any evidence already in the record: a description, a spec table, a review snippet, or a variant label. Cite the source field in the staging note.
- Leave a field blank and flag it rather than guessing when the record provides no evidence. Group all open fields in one reply so the operator can respond once with the missing facts.
- Do not copy a spec from a sibling listing or a category default; each listing's attributes must come from its own record or from what the operator supplies for it.

## Bulk enrichment

- Show the proposed pattern on one or two listings and wait for the operator to confirm it before staging the rest.
- Stage in batches within the per-change item cap and report how many batches remain.
- A bulk run contains only the fields the operator approved; flag anything else noticed along the way as a separate proposal, not part of the batch.

## Stage and preview

- Stage every field change with `stage_listing_update`; group SEO fields (title, meta description, search attributes) in one update and geo fields in a separate one.
- The staging note names the source of each value and, for SEO fields, the keyword phrase and character count for any description or meta field.
- Price, stock, compliance notes, and tax categories are protected fields; do not include them in a merchandising update.
