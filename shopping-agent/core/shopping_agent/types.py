# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The shopping agent's domain models: what a storefront backend returns and the
session records the gates and enrichment read."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from commerce_common.types import ClockContext, remember


class Product(BaseModel):
    """One catalog record, in one of three shapes. Plain: bought as is. Family: carries
    ``options`` (each option with its values in display order) for something sold as a
    fixed set of separately stocked and priced variants. Search returns it and the cart
    refuses it; its ``price`` is its lowest in-stock variant's and ``in_stock`` is true
    while any variant is. Variant: returned in its family's ``ProductDetails.variants`` with its
    own id, price, and stock, ``option_values`` (one value per option), and the family's id
    in ``variant_of``; the cart takes its id like any product's. Only the variants listed
    exist. A price computed per request (dates, party size) goes in
    ``SearchFilters.attributes``; ``docs/backends.md`` has the mapping from common catalog
    models, the family size limit, and what else is not a variant."""

    product_id: str
    title: str
    brand: str | None = None
    price: float
    currency: str = "USD"
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = None
    image_url: str | None = None
    category: str | None = None
    labels: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    in_stock: bool = True
    short_description: str | None = None
    options: dict[str, list[str]] = Field(default_factory=dict)
    option_values: dict[str, str] = Field(default_factory=dict)
    variant_of: str | None = None

    @property
    def has_options(self) -> bool:
        """True for a family: the cart takes one of its variants."""
        return bool(self.options)


class ProductDetails(Product):
    long_description: str | None = None
    specs: dict[str, str] = Field(default_factory=dict)
    review_highlights: list[str] = Field(default_factory=list)
    variants: list[Product] = Field(default_factory=list)


class SearchFilters(BaseModel):
    category: str | None = None
    brand: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_rating: float | None = None
    in_stock_only: bool = False
    attributes: dict[str, str] = Field(default_factory=dict)
    sort: Literal["relevance", "price_asc", "price_desc", "rating"] = "relevance"


class CartItem(BaseModel):
    """``quantity`` counts whole units of the record as sold (a pack, a night, a seat);
    goods priced by measured weight are sold here as fixed packs."""

    product_id: str
    title: str
    price: float
    quantity: int = Field(ge=1)
    image_url: str | None = None
    option_values: dict[str, str] = Field(default_factory=dict)
    variant_of: str | None = None

    @property
    def line_total(self) -> float:
        return round(self.price * self.quantity, 2)


class CheckoutHandoff(BaseModel):
    """Where the customer completes the purchase the ``checkout`` card stages: the
    platform's hosted checkout URL for this cart, or one entry per seller on a marketplace
    whose sellers check out separately. Filled by the backend and rendered by the host; the
    model never supplies or sees the URL."""

    url: str
    label: str | None = None  # button text; the host has a default
    seller: str | None = None  # set when the cart hands off per seller


class Cart(BaseModel):
    items: list[CartItem] = Field(default_factory=list)
    currency: str = "USD"

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)

    @property
    def subtotal(self) -> float:
        return round(sum(item.line_total for item in self.items), 2)


class UserPreferences(BaseModel):
    """Supplied by the backend before every turn; nothing the model does writes to it."""

    user_id: str
    display_name: str | None = None
    loyalty_tier: str | None = None
    default_location: str | None = None
    preferences: dict[str, str] = Field(default_factory=dict)


class OrderStatus(StrEnum):
    PROCESSING = "processing"
    SHIPPED = "shipped"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    RETURN_INITIATED = "return_initiated"
    REFUNDED = "refunded"


class OrderItem(BaseModel):
    product_id: str
    title: str
    quantity: int
    price: float
    option_values: dict[str, str] = Field(default_factory=dict)
    variant_of: str | None = None


class Order(BaseModel):
    order_id: str
    status: OrderStatus
    placed_at: datetime
    items: list[OrderItem] = Field(default_factory=list)
    total: float
    currency: str = "USD"
    estimated_delivery: str | None = None
    tracking_url: str | None = None


class Policy(BaseModel):
    policy_id: str
    title: str
    category: str | None = None
    content: str


# ── Quote ────────────────────────────────────────────────────────────────────

class QuoteStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ORDERED = "ordered"


class QuoteItem(BaseModel):
    product_id: str
    line_item_id: str | None = None
    title: str
    quantity: int = Field(ge=1)
    unit_price: float
    negotiated_price: float | None = None

    @property
    def line_total(self) -> float:
        return round((self.negotiated_price or self.unit_price) * self.quantity, 2)


class Quote(BaseModel):
    quote_id: str
    name: str | None = None
    status: QuoteStatus
    items: list[QuoteItem] = Field(default_factory=list)
    currency: str = "USD"
    subtotal: float
    expiry_date: datetime | None = None
    created_at: datetime
    notes: str | None = None
    order_id: str | None = None  # set when status == ordered


# ── Approval ─────────────────────────────────────────────────────────────────

class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECALLED = "recalled"


class ApprovalStep(BaseModel):
    step_number: int
    approver_name: str
    approver_role: str | None = None
    status: ApprovalStatus
    comments: str | None = None
    acted_at: datetime | None = None


class ApprovalRequest(BaseModel):
    request_id: str
    subject_type: Literal["cart", "quote", "order"]
    subject_id: str
    status: ApprovalStatus
    steps: list[ApprovalStep] = Field(default_factory=list)
    submitted_at: datetime
    total_amount: float
    currency: str = "USD"


# ── Asset ─────────────────────────────────────────────────────────────────────

class AssetStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    RETIRED = "retired"
    IN_SERVICE = "in_service"


class Asset(BaseModel):
    asset_id: str
    name: str
    product_id: str | None = None
    serial_number: str | None = None
    service_tag: str | None = None
    status: AssetStatus = AssetStatus.ACTIVE
    category: str | None = None
    purchase_date: datetime | None = None
    warranty_expiry: datetime | None = None
    location: str | None = None
    assigned_to: str | None = None


# ── Promotion ─────────────────────────────────────────────────────────────────

class Promotion(BaseModel):
    promotion_id: str
    name: str
    description: str
    discount_type: Literal["percentage", "fixed", "free_item"]
    discount_value: float
    min_order_amount: float | None = None
    applicable_categories: list[str] = Field(default_factory=list)
    code: str | None = None
    expiry_date: datetime | None = None
    auto_applied: bool = False


# ── Subscription ─────────────────────────────────────────────────────────────

class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING_RENEWAL = "pending_renewal"


class Subscription(BaseModel):
    subscription_id: str
    name: str
    product_id: str | None = None
    asset_id: str | None = None
    status: SubscriptionStatus
    start_date: datetime
    end_date: datetime
    renewal_date: datetime | None = None
    quantity: int = 1
    annual_value: float | None = None
    currency: str = "USD"
    auto_renew: bool = False


class DisclosureRow(BaseModel):
    label: str
    value: str
    note: str | None = None


class Disclosure(BaseModel):
    """A facts box the backend authors in full; the model only chooses the product."""

    title: str
    product_id: str
    rows: list[DisclosureRow]
    sources: list[str] = Field(default_factory=list)
    footnotes: list[str] = Field(default_factory=list)


class FulfillmentOption(BaseModel):
    method: Literal["delivery", "pickup", "shipping"]
    eta: str
    fee: float = 0.0
    location: str | None = None


class PageContext(BaseModel):
    page_type: Literal["home", "search", "product", "cart", "orders", "other"] = "home"
    product_id: str | None = None
    query: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ShoppingSessionContext(ClockContext):
    session_id: str
    user_id: str
    page: PageContext = Field(default_factory=PageContext)


class ShoppingSessionState(BaseModel):
    """Held by the host for the session. ``seen_products`` is the provenance record: cart
    writes accept only ids in it, and presentation payloads are enriched from it."""

    seen_products: dict[str, Product] = Field(default_factory=dict)

    def remember_products(self, products: list[Product]) -> None:
        for product in products:
            remember(self.seen_products, product.product_id, product)
