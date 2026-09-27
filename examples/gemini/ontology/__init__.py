# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Ontology and semantic layer for B2C product merchandising.

Modules:
    schema      — Pydantic models: ProductOntology, SEOContent, GeoContent, MerchandisingResult
    taxonomy    — B2C reference data: categories, fragrance families, geo occasion map, benefit vocab
    bbw_products — BBW-style product fixtures for merchandising demos
"""

from .bbw_products import BBW_PRODUCTS, get_bbw_product
from .schema import (
    FragranceFamily,
    GeoContent,
    MerchandisingResult,
    ProductCategory,
    ProductOntology,
    SemanticAttribute,
    SEOContent,
)

__all__ = [
    "ProductCategory",
    "FragranceFamily",
    "SemanticAttribute",
    "ProductOntology",
    "SEOContent",
    "GeoContent",
    "MerchandisingResult",
    "BBW_PRODUCTS",
    "get_bbw_product",
]
