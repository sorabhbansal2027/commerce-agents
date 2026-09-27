# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""B2C product taxonomy reference data (BBW/body-care style)."""

from __future__ import annotations

# category → typical subcategories
CATEGORY_TAXONOMY: dict[str, list[str]] = {
    "candles": ["3-wick", "single-wick", "mini", "travel", "seasonal"],
    "body_care": ["body lotion", "body cream", "body wash", "body scrub", "hand cream", "lip balm"],
    "home_fragrance": ["wallflowers", "room spray", "car scent", "sachet", "pillow mist"],
    "soaps": ["foaming hand soap", "hand sanitizer", "liquid hand soap", "bar soap", "hand cream"],
    "jewelry": ["necklace", "bracelet", "earrings", "ring", "anklet", "set"],
    "accessories": ["bag", "wallet", "hair accessory", "belt", "keychain"],
    "other": ["gift set", "bundle", "collection"],
}

# fragrance family → notes, moods, occasions
FRAGRANCE_PROFILES: dict[str, dict[str, list[str]]] = {
    "floral": {
        "notes": ["rose", "jasmine", "cherry blossom", "peony", "lilac", "magnolia"],
        "moods": ["romantic", "feminine", "uplifting", "fresh"],
        "occasions": ["gifting", "everyday", "spring", "date night"],
    },
    "fresh": {
        "notes": ["cucumber", "aloe", "green tea", "watermelon", "ocean mist", "linen"],
        "moods": ["clean", "invigorating", "airy", "calming"],
        "occasions": ["everyday", "summer", "gym", "morning ritual"],
    },
    "warm_spicy": {
        "notes": ["cinnamon", "clove", "nutmeg", "amber", "cardamom", "vanilla"],
        "moods": ["cozy", "warming", "festive", "sensual"],
        "occasions": ["fall", "holiday", "gifting", "home decor"],
    },
    "woody": {
        "notes": ["mahogany", "teakwood", "sandalwood", "cedarwood", "dark oak", "patchouli"],
        "moods": ["grounding", "sophisticated", "bold", "masculine"],
        "occasions": ["everyday", "gifting", "home decor", "work"],
    },
    "fruity": {
        "notes": ["strawberry", "peach", "mango", "pineapple", "raspberry", "citrus burst"],
        "moods": ["playful", "bright", "fun", "energetic"],
        "occasions": ["summer", "everyday", "teens", "gifting"],
    },
    "gourmand": {
        "notes": ["vanilla", "caramel", "sugar", "honey", "brown sugar", "coconut"],
        "moods": ["sweet", "indulgent", "comforting", "nostalgic"],
        "occasions": ["fall", "winter", "self-care", "gifting"],
    },
    "citrus": {
        "notes": ["lemon", "grapefruit", "orange blossom", "bergamot", "lime", "mandarin"],
        "moods": ["energizing", "fresh", "clean", "vibrant"],
        "occasions": ["morning routine", "summer", "everyday", "gym"],
    },
}

# region → season → occasion tags
GEO_OCCASION_MAP: dict[str, dict[str, list[str]]] = {
    "US-northeast": {
        "winter": ["holiday gifting", "cozy home", "stocking stuffer", "New Year celebration"],
        "spring": ["Easter gift", "spring refresh", "Mother's Day", "bridal shower"],
        "summer": ["beach bag essential", "summer entertaining", "Fourth of July"],
        "fall": ["pumpkin patch", "harvest gifting", "Thanksgiving tablescape", "back-to-school"],
    },
    "US-south": {
        "winter": ["holiday gifting", "warm weather winter", "stocking stuffer"],
        "spring": ["garden party", "Easter brunch", "Derby season", "Mother's Day"],
        "summer": ["pool day", "barbecue entertaining", "beach weekend"],
        "fall": ["tailgate season", "fall refresh", "Thanksgiving hosting"],
    },
    "US-west": {
        "winter": ["holiday gifting", "ski weekend", "hygge at home"],
        "spring": ["farmers market finds", "outdoor entertaining", "spring clean"],
        "summer": ["hiking essential", "beach day", "yoga retreat"],
        "fall": ["wine country weekend", "harvest season", "wellness ritual"],
    },
    "UK": {
        "winter": ["Christmas gifting", "Boxing Day treat", "cosy night in", "New Year's Eve"],
        "spring": ["Mother's Day UK", "Easter pressie", "spring bank holiday"],
        "summer": ["garden party", "Wimbledon treat", "summer holiday prep"],
        "fall": ["bonfire night", "autumn refresh", "back to school"],
    },
    "CA": {
        "winter": ["holiday gifting", "après-ski", "stocking stuffer"],
        "spring": ["Victoria Day weekend", "spring refresh", "Mother's Day"],
        "summer": ["Canada Day", "cottage country", "patio season"],
        "fall": ["Thanksgiving CA", "back-to-school", "harvest gifting"],
    },
}

# category → approved benefit phrases (BBW-style)
BENEFIT_VOCABULARY: dict[str, list[str]] = {
    "candles": [
        "45-hour burn time",
        "hand-poured",
        "paraffin-free wax blend",
        "lead-free cotton wick",
        "three-wick for even burn",
        "recyclable glass vessel",
        "premium fragrance load",
        "fills a room in minutes",
        "giftable packaging",
    ],
    "body_care": [
        "24-hour moisture",
        "dermatologist-tested",
        "shea butter enriched",
        "absorbs quickly, non-greasy",
        "allergy-tested",
        "paraben-free formula",
        "vegan formulation",
        "locks in hydration all day",
        "lightweight, layerable scent",
    ],
    "home_fragrance": [
        "lasts up to 30 days",
        "plug-in convenience",
        "adjustable fragrance intensity",
        "room-filling coverage",
        "non-aerosol spray",
        "travel-friendly size",
        "instant freshness",
        "long-lasting scent trail",
    ],
    "soaps": [
        "moisturizing formula",
        "kills 99.9% of common germs",
        "gentle on sensitive skin",
        "no harsh sulfates",
        "dermatologist-tested",
        "rich lather",
        "biodegradable formula",
        "pump-and-go convenience",
    ],
    "jewelry": [
        "tarnish-resistant",
        "hypoallergenic",
        "rhodium-plated for lasting shine",
        "nickel-free",
        "lobster-claw clasp",
        "adjustable fit",
        "gift-box included",
        "layerable design",
    ],
    "accessories": [
        "premium craftsmanship",
        "durable hardware",
        "full-grain material",
        "interior organization pockets",
        "versatile everyday style",
        "lightweight carry",
    ],
    "other": [
        "curated gift set",
        "limited-edition collection",
        "beautifully packaged",
        "value bundle",
    ],
}

# category → keyword template strings (placeholders filled at runtime)
SEO_KEYWORD_TEMPLATES: dict[str, list[str]] = {
    "candles": [
        "{fragrance} scented candle",
        "luxury candle gift",
        "long-burning candle",
        "three-wick candle",
        "home fragrance candle",
        "{brand} candle",
    ],
    "body_care": [
        "{fragrance} body lotion",
        "moisturizing body cream",
        "24 hour moisture lotion",
        "shea butter body lotion",
        "{brand} body care",
        "hydrating body wash",
    ],
    "home_fragrance": [
        "home fragrance {fragrance}",
        "wallflower refill",
        "room spray freshener",
        "long-lasting home scent",
        "{brand} home fragrance",
        "plug-in air freshener",
    ],
    "soaps": [
        "gentle hand soap",
        "moisturizing hand wash",
        "antibacterial hand sanitizer",
        "foaming hand soap {fragrance}",
        "{brand} hand soap",
        "luxury hand cleanser",
    ],
    "jewelry": [
        "{style} {type} jewelry",
        "layering necklace",
        "dainty gold bracelet",
        "gift jewelry",
        "tarnish-resistant jewelry",
        "{brand} jewelry",
    ],
    "accessories": [
        "{style} bag",
        "everyday {type}",
        "premium {material} {type}",
        "{brand} accessories",
    ],
    "other": [
        "{brand} gift set",
        "luxury gift bundle",
        "curated gift collection",
    ],
}
