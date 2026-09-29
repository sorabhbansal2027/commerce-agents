# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""BBW-style product fixtures for merchandising demos.

These 12 products span the four core B2C categories (candles, body care,
home fragrance, soaps) so merchandising tools demo against authentic B2C
data rather than the DreamHaus jewelry catalog.

The tools call get_bbw_product() first; if None, they fall through to the
SFCC backend.
"""

from __future__ import annotations

BBW_PRODUCTS: list[dict] = [
    # ── Candles ──────────────────────────────────────────────────────────────
    {
        "id": "BBW-C001",
        "title": "Mahogany Teakwood 3-Wick Candle",
        "description": (
            "A bold, woody signature scent blending rich mahogany, "
            "dark teakwood, and smoked oak. Hand-poured in a heavy glass vessel."
        ),
        "category": "candles",
        "price": 26.95,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-c001/400/400",
        "attributes": {
            "size": "14.5 oz",
            "burn_time": "45 hours",
            "wick_type": "3-wick cotton",
            "wax_blend": "paraffin-free",
            "fragrance_family": "woody",
            "scent_notes": "mahogany, dark teakwood, smoked oak",
            "vessel_material": "heavy glass",
        },
    },
    {
        "id": "BBW-C002",
        "title": "White Barn Flannel 3-Wick Candle",
        "description": (
            "Warm, comforting flannel layered with clean white woods and "
            "a hint of soft musk. A cozy staple for any room."
        ),
        "category": "candles",
        "price": 26.95,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-c002/400/400",
        "attributes": {
            "size": "14.5 oz",
            "burn_time": "45 hours",
            "wick_type": "3-wick cotton",
            "wax_blend": "paraffin-free",
            "fragrance_family": "fresh",
            "scent_notes": "flannel, white woods, soft musk",
            "vessel_material": "heavy glass",
        },
    },
    {
        "id": "BBW-C003",
        "title": "Vanilla Birch Single-Wick Candle",
        "description": (
            "Sweet vanilla meets airy birch wood — a clean, warm scent that "
            "fills your home with effortless calm."
        ),
        "category": "candles",
        "price": 14.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-c003/400/400",
        "attributes": {
            "size": "7 oz",
            "burn_time": "25-45 hours",
            "wick_type": "single-wick cotton",
            "wax_blend": "paraffin-free",
            "fragrance_family": "gourmand",
            "scent_notes": "vanilla cream, birch wood, soft amber",
            "vessel_material": "glass jar",
        },
    },
    # ── Body Care ─────────────────────────────────────────────────────────────
    {
        "id": "BBW-B001",
        "title": "Japanese Cherry Blossom Body Lotion",
        "description": (
            "Ultra-shea body lotion with delicate notes of fresh cherry blossom, "
            "Asian pear, and sandalwood. Absorbs quickly without any greasy residue."
        ),
        "category": "body_care",
        "price": 14.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-b001/400/400",
        "attributes": {
            "size": "8 fl oz",
            "skin_type": "all",
            "fragrance_family": "floral",
            "scent_notes": "cherry blossom, Asian pear, sandalwood",
            "key_ingredient": "shea butter",
            "finish": "non-greasy",
            "formula": "24-hour moisture",
        },
    },
    {
        "id": "BBW-B002",
        "title": "Warm Vanilla Sugar Body Cream",
        "description": (
            "Rich, indulgent body cream with warm vanilla and brown sugar. "
            "Intensely nourishing for dry skin — perfect for an everyday treat."
        ),
        "category": "body_care",
        "price": 16.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-b002/400/400",
        "attributes": {
            "size": "8 fl oz",
            "skin_type": "dry to normal",
            "fragrance_family": "gourmand",
            "scent_notes": "warm vanilla, brown sugar, musk",
            "key_ingredient": "shea butter, cocoa butter",
            "finish": "rich cream",
            "formula": "intensive moisture",
        },
    },
    {
        "id": "BBW-B003",
        "title": "Eucalyptus Spearmint Body Wash",
        "description": (
            "Invigorating body wash with cooling eucalyptus and refreshing spearmint. "
            "Part of the Aromatherapy collection — helps you unwind and de-stress."
        ),
        "category": "body_care",
        "price": 13.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-b003/400/400",
        "attributes": {
            "size": "10 fl oz",
            "skin_type": "all",
            "fragrance_family": "fresh",
            "scent_notes": "eucalyptus, spearmint, sea salt",
            "key_ingredient": "essential oils",
            "collection": "Aromatherapy",
            "benefit": "stress relief",
        },
    },
    # ── Home Fragrance ────────────────────────────────────────────────────────
    {
        "id": "BBW-H001",
        "title": "Mahogany Teakwood Wallflower Plug Refill",
        "description": (
            "Continuously fragrance your home with the bold, woody scent of "
            "Mahogany Teakwood. Each refill lasts up to 30 days."
        ),
        "category": "home_fragrance",
        "price": 7.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-h001/400/400",
        "attributes": {
            "size": "0.8 fl oz",
            "duration": "30 days",
            "fragrance_family": "woody",
            "scent_notes": "mahogany, teakwood, dark oak",
            "type": "wallflower refill",
            "compatibility": "all Wallflowers plugs",
        },
    },
    {
        "id": "BBW-H002",
        "title": "Fresh Balsam Room Spray",
        "description": (
            "Bring the crisp, clean scent of a winter forest indoors. "
            "Fresh Balsam room spray delivers instant festive freshness."
        ),
        "category": "home_fragrance",
        "price": 9.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-h002/400/400",
        "attributes": {
            "size": "5.3 fl oz",
            "fragrance_family": "fresh",
            "scent_notes": "fresh balsam, crisp mountain air, pine needles",
            "type": "room spray",
            "seasonal": "holiday / winter",
            "format": "non-aerosol pump",
        },
    },
    {
        "id": "BBW-H003",
        "title": "Aromatherapy Sleep Lavender Vanilla Pillow Mist",
        "description": (
            "A calming blend of lavender and vanilla in an ultra-fine mist. "
            "Spray on pillows and linens before bed for a restful night's sleep."
        ),
        "category": "home_fragrance",
        "price": 11.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-h003/400/400",
        "attributes": {
            "size": "5.3 fl oz",
            "fragrance_family": "floral",
            "scent_notes": "lavender, vanilla, chamomile",
            "type": "pillow mist",
            "collection": "Aromatherapy Sleep",
            "benefit": "sleep support",
            "key_ingredient": "lavender essential oil",
        },
    },
    # ── Soaps ─────────────────────────────────────────────────────────────────
    {
        "id": "BBW-S001",
        "title": "Gentle Foaming Hand Soap — Aloe Water & Agave",
        "description": (
            "A light, moisturizing foaming hand soap with fresh aloe water and "
            "agave. Gentle enough for frequent washing, with a clean, botanical scent."
        ),
        "category": "soaps",
        "price": 9.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-s001/400/400",
        "attributes": {
            "size": "8.75 fl oz",
            "skin_type": "sensitive",
            "fragrance_family": "fresh",
            "scent_notes": "aloe water, agave nectar, fresh greens",
            "type": "foaming hand soap",
            "key_ingredient": "aloe vera",
            "formula": "moisturizing, sulfate-free",
        },
    },
    {
        "id": "BBW-S002",
        "title": "PocketBac Anti-Bacterial Hand Gel — Iced Pineapple",
        "description": (
            "Pocket-sized antibacterial hand gel with a bright, tropical iced pineapple "
            "scent. Kills 99.9% of most common germs — no water needed."
        ),
        "category": "soaps",
        "price": 1.95,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-s002/400/400",
        "attributes": {
            "size": "1 fl oz",
            "fragrance_family": "fruity",
            "scent_notes": "iced pineapple, tropical citrus, coconut water",
            "type": "hand sanitizer gel",
            "active_ingredient": "68% ethyl alcohol",
            "format": "travel-size",
        },
    },
    {
        "id": "BBW-S003",
        "title": "Shea-Enriched Moisturizing Hand Cream — Rose Water",
        "description": (
            "A thick, luxurious hand cream infused with shea butter and delicate "
            "rose water. Softens and protects even the driest hands."
        ),
        "category": "soaps",
        "price": 8.50,
        "currency": "USD",
        "status": "active",
        "image_url": "https://picsum.photos/seed/bbw-s003/400/400",
        "attributes": {
            "size": "2 fl oz",
            "skin_type": "dry to very dry",
            "fragrance_family": "floral",
            "scent_notes": "rose water, peony, soft musk",
            "type": "hand cream",
            "key_ingredient": "shea butter",
            "formula": "intensive moisture",
        },
    },
]

# Index for O(1) lookup
_INDEX: dict[str, dict] = {p["id"]: p for p in BBW_PRODUCTS}


def get_bbw_product(listing_id: str) -> dict | None:
    """Return a BBW fixture product by ID, or None if not found."""
    return _INDEX.get(listing_id)
