# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The shopping agent's shared library. This root exports what an adopter's backend and
host code use: the domain types, ``StorefrontBackend``, the config, and the tool-result
serializers. The prompt, tool contracts, and gates live in the submodules.
"""

from .backend import NotOffered, StorefrontBackend, Unavailable
from .config import ShoppingAgentConfig
from .serialization import cart_payload, compact_product, search_result_text
from .types import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStep,
    Asset,
    AssetStatus,
    Cart,
    CartItem,
    CheckoutHandoff,
    Disclosure,
    DisclosureRow,
    FulfillmentOption,
    Order,
    OrderItem,
    OrderStatus,
    PageContext,
    Policy,
    Product,
    ProductDetails,
    Promotion,
    Quote,
    QuoteItem,
    QuoteStatus,
    SearchFilters,
    ShoppingSessionContext,
    ShoppingSessionState,
    Subscription,
    SubscriptionStatus,
    UserPreferences,
)

__all__ = [
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalStep",
    "Asset",
    "AssetStatus",
    "Cart",
    "CheckoutHandoff",
    "CartItem",
    "Disclosure",
    "DisclosureRow",
    "FulfillmentOption",
    "NotOffered",
    "Order",
    "OrderItem",
    "OrderStatus",
    "PageContext",
    "Policy",
    "Product",
    "ProductDetails",
    "Promotion",
    "Quote",
    "QuoteItem",
    "QuoteStatus",
    "SearchFilters",
    "ShoppingAgentConfig",
    "ShoppingSessionContext",
    "ShoppingSessionState",
    "StorefrontBackend",
    "Subscription",
    "SubscriptionStatus",
    "Unavailable",
    "UserPreferences",
    "cart_payload",
    "compact_product",
    "search_result_text",
]
