# Skill: Catalog Operations

Handles requests to browse, search, read, and update product listings.

## When this skill applies

- Operator asks to find, view, or list products
- Operator asks to edit a listing title, description, or attributes
- Operator asks to check or set a product's price or status
- Operator asks to stage or discard a content change

## Flow

1. If no listing ID is known, call `search_listings` first. Never guess IDs.
2. Call `get_listing` to read the full record before editing.
3. Identify which fields need to change. Do not include unchanged fields in `stage_listing_update`.
4. Call `stage_listing_update` with only the changed fields and a one-sentence note.
5. Show a summary of what was staged and ask for approval.
6. Call `apply_change` only after the operator explicitly approves.

## Rules

- Never call `stage_listing_update` with a `listing_id` that was not returned by `search_listings` or `get_listing` this session.
- Never call `apply_change` before `approve_change` is called for that change.
- If a listing has variants (sizes, colors), name the specific variant — do not edit the parent.
- Edits to `price` and `currency` require the operator to confirm the figure before staging.
