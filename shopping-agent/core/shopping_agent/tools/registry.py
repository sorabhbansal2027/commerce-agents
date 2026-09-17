# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The shopping agent's tool contracts, in a fixed order. The list depends only on the
deployment config, so it is the same bytes on every request; whether a capability can
serve a call is decided in the executor. A description covers one tool; rules that span
tools live in the prompt or a skill."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from commerce_common.execution import LOAD_SKILL, with_status
from commerce_common.presentation import PresentationExtension

from ..config import ShoppingAgentConfig

_SESSION_PRODUCT_ID = "product_id returned by a tool this session."
_STATUS_READER = "the customer"


def _product_id(role: str = _SESSION_PRODUCT_ID) -> dict[str, Any]:
    return {"type": "string", "description": role}


def _filters_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Constraints the customer stated; leave guesses in the query.",
        "properties": {
            "category": {"type": "string", "description": "Catalog category name."},
            "min_price": {"type": "number", "description": "Lowest acceptable price."},
            "max_price": {"type": "number", "description": "Price ceiling the customer stated."},
            "min_rating": {"type": "number", "description": "Lowest acceptable average rating."},
            "attributes": {
                "type": "object",
                "description": 'Attribute or option filters as key/value pairs, e.g. {"material": "wool"}.',
                "additionalProperties": {"type": "string"},
            },
            "sort": {
                "type": "string",
                "enum": ["relevance", "price_asc", "price_desc", "rating"],
                "description": "Result order; relevance unless the customer asked otherwise.",
            },
        },
        "additionalProperties": False,
    }


def _title(what: str) -> dict[str, Any]:
    return {"type": "string", "maxLength": 80, "description": f"Short heading for the {what}."}


# Paths that send no per-request Session context block (the Agent SDK toolset and the MCP
# server) carry the profile inline in get_preferences, so two descriptions point there.
INLINE_CONTEXT_DESCRIPTIONS: dict[str, str] = {
    "get_preferences": (
        "The customer's profile, saved preferences, and remembered facts from past "
        "conversations. Call this once near the start of a conversation and apply it as "
        "defaults, not hard rules."
    ),
    "recall_memories": (
        "Search the customer's saved memory for facts not already returned by "
        "get_preferences (older preferences, sizes, gift history, recurring needs). "
        "Use this only when something older or more specific would change your "
        "recommendation."
    ),
}


def build_tools(
    config: ShoppingAgentConfig,
    skill_names: list[str],
    extra_presentation_tools: Sequence[PresentationExtension] = (),
) -> list[dict[str, Any]]:
    """The tool list for one deployment: built-ins in a fixed order, less the systems the
    config switches off, then the deployment's presentation extensions in the order
    given, then web search when enabled. Every tool but the presentation tools and web
    search takes the optional ``status`` line first."""

    tools: list[dict[str, Any]] = [
        {
            "name": LOAD_SKILL,
            "description": (
                "Load the rules of the flow whose entry in the skill index the request "
                "matches; they are not in your prompt. Call it in the same round as the "
                "flow's first read and follow them for the rest of the flow."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "enum": sorted(skill_names),
                        "description": "Name of the skill as listed in the index.",
                    },
                },
                "required": ["skill_name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_products",
            "description": (
                "Search the catalog; returns products with id, title, brand, price, rating, "
                "and availability; a product with options shows its lowest in-stock price "
                "and its options. Use a specific query and put stated constraints in filters. "
                "Run one search per distinct item a request names."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, in the catalog's vocabulary.",
                    },
                    "filters": _filters_schema(),
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": config.max_search_results,
                        "description": "Maximum results to return.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_product_details",
            "description": (
                "Full details for one product: description, specs, review highlights, and "
                "for a product with options, its variants with their ids, prices, and stock. "
                "Use for a question about one product, before comparing finalists or choosing "
                "a variant, and for a reference shaped like a catalog id."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"product_id": _product_id("Catalog product_id to look up.")},
                "required": ["product_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_cart",
            "description": "Current cart contents with quantities and subtotal.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "add_to_cart",
            "description": (
                "Add a product, or the chosen variant of a product with options, by a "
                "product_id a catalog or order tool returned this session; quantity defaults "
                "to 1. If the result says the item is unavailable, say so and offer an "
                "alternative; add that only once the customer chooses it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "product_id": _product_id(),
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Units to add; omit for one.",
                    },
                },
                "required": ["product_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "update_cart_item",
            "description": "Set the quantity of an item that is already in the cart.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "product_id": _product_id("product_id of a line already in the cart."),
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "New quantity for the line.",
                    },
                },
                "required": ["product_id", "quantity"],
                "additionalProperties": False,
            },
        },
        {
            "name": "remove_from_cart",
            "description": "Remove an item from the cart.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "product_id": _product_id("product_id of the line to remove."),
                },
                "required": ["product_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_preferences",
            "description": (
                "The customer's profile and saved preferences. Usually already in the "
                "Session context block; call this only when it is missing there."
            ),
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_orders",
            "description": (
                "Recent orders with status and estimated delivery. Use for a status question "
                "that names no order and for a request to buy something again."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Maximum orders to return.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_order_status",
            "description": "Status, items, and tracking for one order the customer named.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order id to look up."},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_policies",
            "description": (
                "Search the store's own terms and help content: returns, shipping, "
                "warranties, membership, fees, and the buying guides."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The term or topic to look up."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_fulfillment_options",
            "description": (
                "Delivery and pickup options, with their dates, for given products at the "
                "customer's location."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                        "description": "product_ids to quote options for.",
                    },
                },
                "required": ["product_ids"],
                "additionalProperties": False,
            },
        },
        {
            "name": "save_memory",
            "description": (
                "Save a durable fact about the customer when they ask you to remember "
                "something or state a standing rule about how they shop. An ask to remember "
                "is the memory-personalization flow, and its skill says how the fact is "
                "worded: read it in the same round. Save the need an item reveals, never "
                "product or policy text."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Topic key; reuse an existing key to replace its value.",
                    },
                    "value": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "The fact, worded to stand on its own later.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "constraint", "context"],
                        "description": (
                            "constraint for a rule picks must respect; else preference or context."
                        ),
                    },
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
        {
            "name": "recall_memories",
            "description": (
                "Search the customer's saved facts that are not in the Session context "
                "block: older preferences, sizes, past recipients, recurring needs. Use it "
                "when such a fact would change your recommendation."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "maxLength": 100,
                        "description": "Topic to search for, in a few words.",
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        },
        # ── Quotes ───────────────────────────────────────────────────────────────
        {
            "name": "get_quotes",
            "description": (
                "List the buyer's recent quotes with id, name, status, subtotal, and expiry. "
                "After getting the list, always call present_quote for each quote so the buyer "
                "sees a visual card with line items and images — do not describe quotes in plain text."
            ),
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_quote",
            "description": "Full detail for one quote: line items, negotiated prices, status, and expiry. Use when the buyer asks for a specific quote by id and you do not already have its details.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "quote_id": {"type": "string", "description": "Quote id to retrieve."},
                },
                "required": ["quote_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "create_quote",
            "description": (
                "Convert the current cart into a draft quote. Before calling: (1) call "
                "get_cart to retrieve and show the buyer the current cart items, (2) ask "
                "the buyer to confirm they want to create a quote from those specific items. "
                "Only call create_quote after the buyer confirms. This is a write."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 120, "description": "Quote name."},
                    "notes": {"type": "string", "maxLength": 500, "description": "Optional notes for the sales rep."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "submit_quote",
            "description": (
                "Submit a draft quote for sales-rep review or internal approval. "
                "Confirm once before calling."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "quote_id": {"type": "string", "description": "Draft quote id to submit."},
                },
                "required": ["quote_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "update_quote_item",
            "description": (
                "Change the quantity of a line item in a draft quote. "
                "Use when the buyer asks to adjust quantities on an existing quote."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "quote_id": {"type": "string", "description": "The quote id."},
                    "line_item_id": {"type": "string", "description": "The line item id from the quote's items list."},
                    "quantity": {"type": "integer", "minimum": 1, "description": "New quantity."},
                },
                "required": ["quote_id", "line_item_id", "quantity"],
                "additionalProperties": False,
            },
        },
        {
            "name": "convert_quote_to_order",
            "description": (
                "Place an order from an approved quote. Confirm once before calling — "
                "this places the order and cannot be undone here."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "quote_id": {"type": "string", "description": "Approved quote id to convert."},
                },
                "required": ["quote_id"],
                "additionalProperties": False,
            },
        },
        # ── Approvals ─────────────────────────────────────────────────────────────
        {
            "name": "submit_for_approval",
            "description": (
                "Submit a cart, quote, or order into the configured approval chain. "
                "State what is being submitted and the total amount, then confirm once before calling."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "subject_type": {
                        "type": "string",
                        "enum": ["cart", "quote", "order"],
                        "description": "What is being submitted for approval.",
                    },
                    "subject_id": {
                        "type": "string",
                        "description": "Id of the cart session, quote, or order.",
                    },
                },
                "required": ["subject_type", "subject_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_approval_status",
            "description": "Current state of an approval request: each step, approver, and any comments.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "Approval request id."},
                },
                "required": ["request_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "recall_approval_request",
            "description": (
                "Withdraw a pending approval request. Confirm once before calling. "
                "Only valid on requests with status 'pending'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "Pending approval request id."},
                },
                "required": ["request_id"],
                "additionalProperties": False,
            },
        },
        # ── Assets ────────────────────────────────────────────────────────────────
        {
            "name": "get_assets",
            "description": (
                "Installed-base assets the account owns — laptops, servers, network gear — "
                "with serial numbers, service tags, and warranty status. Filter by category, "
                "status, or a search query. After fetching, always call present_assets to show "
                "the visual card — do not list assets in plain text."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Product category to filter by."},
                    "status": {
                        "type": "string",
                        "enum": ["active", "inactive", "retired", "in_service"],
                        "description": "Asset status to filter by.",
                    },
                    "query": {"type": "string", "description": "Free-text search across name, serial, service tag."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_asset_details",
            "description": "Full detail for one asset: purchase date, warranty expiry, location, and assigned user.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string", "description": "Asset id, serial number, or service tag."},
                },
                "required": ["asset_id"],
                "additionalProperties": False,
            },
        },
        # ── Promotions ────────────────────────────────────────────────────────────
        {
            "name": "get_promotions",
            "description": (
                "Available promotions, volume discounts, and rebates for this account. "
                "Filter by product category. Use before presenting a large cart or when the "
                "buyer asks about deals or discounts."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category to filter promotions by."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "apply_promotion",
            "description": "Apply a promotion by id or promo code to the current cart; returns the updated cart.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "promotion_id": {"type": "string", "description": "Promotion id to apply."},
                    "code": {"type": "string", "description": "Promo code to apply."},
                },
                "additionalProperties": False,
            },
        },
        # ── Subscriptions ─────────────────────────────────────────────────────────
        {
            "name": "get_subscriptions",
            "description": (
                "Active service contracts, extended warranties, and managed services for the account. "
                "Filter by status — use 'expiring_soon' to proactively surface renewals. "
                "After fetching, always call present_subscriptions for the visual card."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "expiring_soon", "expired", "cancelled", "pending_renewal"],
                        "description": "Filter by subscription status.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_subscription_details",
            "description": "Full detail for one subscription: dates, value, auto-renew status, and linked asset.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "subscription_id": {"type": "string", "description": "Subscription id."},
                },
                "required": ["subscription_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "renew_subscription",
            "description": (
                "Initiate a renewal for a subscription. Confirm once before calling — "
                "state the subscription name and amount. Returns the resulting quote or order."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "subscription_id": {"type": "string", "description": "Subscription id to renew."},
                },
                "required": ["subscription_id"],
                "additionalProperties": False,
            },
        },
    ]
    presentation: list[dict[str, Any]] = [
        {
            "name": "present_products",
            "description": (
                "Show products from this session's results as cards; the UI fills in title, "
                "price, and image. Layout: carousel by default, grid to scan many options, "
                "list when order matters. Each pick's reason is the one judgment of yours on "
                "the card."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": _title("set of cards"),
                    "layout": {
                        "type": "string",
                        "enum": ["carousel", "grid", "list"],
                        "description": "Card layout; carousel when omitted.",
                    },
                    "picks": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "description": "Products to show, recommended pick first.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": _product_id(),
                                "reason": {
                                    "type": "string",
                                    "maxLength": 140,
                                    "description": "One clause tying the pick to a stated need.",
                                },
                            },
                            "required": ["product_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["picks"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_comparison",
            "description": (
                "Compare 2-4 finalists side by side, with pros, cons, and what each is best "
                "for. Use it once the customer has narrowed to them or asks how they differ; "
                "a fresh shortlist goes through present_products. The UI adds the price "
                "delta; your text says what the extra money buys."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": _title("comparison"),
                    "entries": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "The finalists being compared.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": _product_id(),
                                "pros": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 4,
                                    "description": "Short advantages, from tool results.",
                                },
                                "cons": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 3,
                                    "description": "Short drawbacks, from tool results.",
                                },
                                "best_for": {
                                    "type": "string",
                                    "maxLength": 80,
                                    "description": "Who or what this option suits best.",
                                },
                            },
                            "required": ["product_id"],
                            "additionalProperties": False,
                        },
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 6,
                        "description": "The dimensions the customer is weighing.",
                    },
                    "recommended_product_id": _product_id("The entry you recommend."),
                },
                "required": ["entries"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_plan",
            "description": (
                "Show a plan toward the customer's goal as steps with products attached. "
                "When no step will ever carry a product, the answer is know-how; "
                "use present_guide."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": _title("plan"),
                    "intro": {
                        "type": "string",
                        "maxLength": 240,
                        "description": "One or two lines of framing, assumptions included.",
                    },
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "description": "The plan's steps, in working order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "maxLength": 120,
                                    "description": "Step name in the customer's words.",
                                },
                                "detail": {
                                    "type": "string",
                                    "maxLength": 240,
                                    "description": "One line on what the step covers.",
                                },
                                "product_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 8,
                                    "description": "Items for this step, your pick first.",
                                },
                            },
                            "required": ["label"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "steps"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_guide",
            "description": (
                "Show a how-to, an idea, or an itinerary as a card of short sections. Use "
                "for guidance that is not a product plan; list sources when web content "
                "informed it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": _title("guide"),
                    "sections": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "description": "Sections of one to three sentences each.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {
                                    "type": "string",
                                    "maxLength": 80,
                                    "description": "Section heading.",
                                },
                                "body": {
                                    "type": "string",
                                    "maxLength": 600,
                                    "description": "Section text.",
                                },
                            },
                            "required": ["heading", "body"],
                            "additionalProperties": False,
                        },
                    },
                    "related_product_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 8,
                        "description": "Products from this session the guide relates to.",
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 5,
                        "description": "Guides or pages the sections were drawn from.",
                    },
                },
                "required": ["title", "sections"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_order_status",
            "description": (
                "Show the status card for one order; the UI fills in the order data. Every "
                "answer about where an order stands goes through it. When several orders "
                "are in flight, send one card per order in the same round."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order id from get_orders or get_order_status.",
                    },
                    "summary": {
                        "type": "string",
                        "maxLength": 300,
                        "description": "Current state and expected date, in a sentence.",
                    },
                    "next_step": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "The one concrete thing the customer can do next.",
                    },
                },
                "required": ["order_id", "summary"],
                "additionalProperties": False,
            },
        },
        {
            "name": "checkout",
            "description": (
                "Stage the current cart as an order summary the customer confirms in the "
                "app; it places no order and charges nothing. Use only when the customer "
                "asks to check out."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "maxLength": 300,
                        "description": "Anything the customer should check before confirming.",
                    },
                    "fulfillment_method": {
                        "type": "string",
                        "enum": ["delivery", "pickup", "shipping"],
                        "description": "Method the customer chose, when they chose one.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "present_quote",
            "description": (
                "Show a visual quote card with product images, line items, subtotal, status badge, "
                "and expiry. Call this for every quote after get_quotes or get_quote — "
                "never describe a quote in plain text when you have its id."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "quote_id": {"type": "string", "description": "Quote id from this session."},
                    "summary": {"type": "string", "maxLength": 300, "description": "One-sentence status summary."},
                    "next_step": {"type": "string", "maxLength": 200, "description": "What the buyer should do next."},
                },
                "required": ["quote_id", "summary"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_approval_status",
            "description": (
                "Show the approval request with each step's approver, status, and any comments."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "Approval request id."},
                    "summary": {"type": "string", "maxLength": 300, "description": "One-sentence status summary."},
                    "next_step": {"type": "string", "maxLength": 200, "description": "What happens next."},
                },
                "required": ["request_id", "summary"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_assets",
            "description": (
                "Show installed-base assets as a visual list with warranty badges "
                "(expired/expiring-soon/ok). Always call after get_assets — never list assets in plain text."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 80, "description": "Card title."},
                    "entries": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "asset_id": {"type": "string"},
                                "highlight": {"type": "string", "maxLength": 120, "description": "One key fact about this asset."},
                            },
                            "required": ["asset_id"],
                            "additionalProperties": False,
                        },
                    },
                    "warranty_alert": {"type": "string", "maxLength": 200, "description": "Summary of assets with expiring warranties."},
                },
                "required": ["entries"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_promotions",
            "description": (
                "Show available promotions and discounts. Use after get_promotions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 80},
                    "picks": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "promotion_id": {"type": "string"},
                                "callout": {"type": "string", "maxLength": 120, "description": "Why this promotion matters now."},
                            },
                            "required": ["promotion_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["picks"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_subscriptions",
            "description": (
                "Show service contracts and subscriptions as a visual card with status badges and "
                "renewal alerts. Always call after get_subscriptions — never list contracts in plain text."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 80},
                    "entries": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "subscription_id": {"type": "string"},
                                "highlight": {"type": "string", "maxLength": 120, "description": "Key status fact, e.g. 'expires in 45 days'."},
                            },
                            "required": ["subscription_id"],
                            "additionalProperties": False,
                        },
                    },
                    "renewal_alert": {"type": "string", "maxLength": 200, "description": "Summary of subscriptions needing renewal action."},
                },
                "required": ["entries"],
                "additionalProperties": False,
            },
        },
        {
            "name": "present_suggestions",
            "description": (
                "Give the turn its 1-4 chips; it ends the reply. Call it in the same round "
                "as the turn's last component, without waiting for that component's result. "
                "Alone, after the text, only on a turn with no component (a terms answer, a "
                "clarifying question, a confirmed add or save)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "suggestions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 4,
                        "description": (
                            "1-4 chips, each a brief imperative and each a different kind "
                            "of step; leave out anything this turn already displayed."
                        ),
                    },
                },
                "required": ["suggestions"],
                "additionalProperties": False,
            },
        },
    ]

    absent = config.absent_tools()
    tools = [with_status(tool, _STATUS_READER) for tool in tools if tool["name"] not in absent]
    tools += [tool for tool in presentation if tool["name"] not in absent]

    if config.enable_disclosures:
        tools.append(
            {
                "name": "present_disclosure",
                "description": (
                    "Show the standardized disclosure box for one product: its terms, fees, "
                    "and typical performance as the store's systems state them. Use when an "
                    "answer states fees or regulated terms for a product, the customer's "
                    "current plan included."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "product_id": _product_id("The product the terms attach to."),
                        "title": _title("disclosure box"),
                    },
                    "required": ["product_id"],
                    "additionalProperties": False,
                },
            }
        )

    base_names = {tool["name"] for tool in tools}
    for extension in extra_presentation_tools:
        if extension.name in base_names:
            raise ValueError(
                f"presentation extension {extension.name!r} collides with a built-in tool"
            )
        base_names.add(extension.name)
        tools.append(extension.tool_definition())

    if config.enable_web_search:
        tools.append(
            {
                # Pinned; newer web_search versions exist, so re-run evals before moving.
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }
        )

    return tools
