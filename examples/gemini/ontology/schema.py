# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Pydantic schemas for B2C product ontology and merchandising content."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ProductCategory(StrEnum):
    CANDLES = "candles"
    BODY_CARE = "body_care"
    HOME_FRAGRANCE = "home_fragrance"
    SOAPS = "soaps"
    JEWELRY = "jewelry"
    ACCESSORIES = "accessories"
    OTHER = "other"


class FragranceFamily(StrEnum):
    FLORAL = "floral"
    FRESH = "fresh"
    WARM_SPICY = "warm_spicy"
    WOODY = "woody"
    FRUITY = "fruity"
    GOURMAND = "gourmand"
    CITRUS = "citrus"


class SemanticAttribute(BaseModel):
    key: str
    value: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "ai_generated"


class ProductOntology(BaseModel):
    listing_id: str
    category: ProductCategory | None = None
    subcategory: str | None = None
    # ["celestial", "minimalist", "layerable"]
    style_tags: list[str] = Field(default_factory=list)
    # ["gifting", "everyday", "holiday"]
    occasions: list[str] = Field(default_factory=list)
    # ["tarnish-resistant", "hypoallergenic", "24-hour moisture"]
    benefits: list[str] = Field(default_factory=list)
    # "self-gifter" | "gift-giver" | "home decorator"
    target_persona: str | None = None
    semantic_attributes: list[SemanticAttribute] = Field(default_factory=list)


class SEOContent(BaseModel):
    # keyword-first, ≤60 chars
    title: str
    # ≤155 chars
    meta_description: str
    h1: str
    # primary + 4-6 secondary
    keywords: list[str] = Field(default_factory=list)
    # url-friendly
    slug: str


class GeoContent(BaseModel):
    # "US-northeast" | "US-south" | "US-west" | "UK" | "CA"
    region: str
    title_variant: str | None = None
    # 2-3 sentences, region-adapted
    description_variant: str
    occasion_tags: list[str] = Field(default_factory=list)
    seasonal_notes: str | None = None


class MerchandisingResult(BaseModel):
    listing_id: str
    original_title: str
    # vivid, benefit-led B2C paragraph
    enriched_description: str
    # 3-5 scannable bullets (BBW-style)
    benefits_bullets: list[str] = Field(default_factory=list)
    ontology: ProductOntology
    seo: SEOContent | None = None
    geo_variants: list[GeoContent] = Field(default_factory=list)
    # set if changes were automatically staged
    staged_change_id: str | None = None
