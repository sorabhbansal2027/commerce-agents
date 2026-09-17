# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The shopping agent's tools, one handler each, over the shared executor frame. The
Messages API runtime, the SDK toolset, and the MCP server all execute through this
class, so a tool result is the same bytes on every path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from commerce_common.execution import BaseToolExecutor, Handler, clamp_limit, parse_argument
from commerce_common.memory import MemoryRuntime, memory_fact_payload
from commerce_common.presentation import PresentationExtension
from commerce_common.skills import SkillRegistry
from commerce_common.streaming import ToolOutcome

from .backend import NotOffered, StorefrontBackend, Unavailable
from .config import ShoppingAgentConfig
from .enrichment import PRESENTATION_COMPONENTS
from .fencing import STOREFRONT_FENCE
from .gates import (
    gated_add_to_cart,
    gated_remove_from_cart,
    gated_update_cart_item,
    remember_order_items,
)
from .memory import SHOPPING_MEMORY_EXTRACTION_PROMPT
from .serialization import (
    approval_payload,
    assets_payload,
    cart_payload,
    fulfillment_payload,
    order_payload,
    orders_payload,
    policies_payload,
    product_details_payload,
    promotions_payload,
    quote_payload,
    quotes_payload,
    search_result_text,
    subscriptions_payload,
)
from .types import SearchFilters, ShoppingSessionContext, ShoppingSessionState

MAX_ORDERS = 20
MAX_FULFILLMENT_IDS = 20


def build_memory(
    config: ShoppingAgentConfig, store: Any, write_filter: Any = None
) -> MemoryRuntime:
    """The shopping agent's :class:`MemoryRuntime`: the store under this config, keyed
    by user id, extracting under the shopping prompt."""
    return MemoryRuntime.build(
        config,
        store,
        fence=STOREFRONT_FENCE,
        extraction_prompt=SHOPPING_MEMORY_EXTRACTION_PROMPT,
        write_filter=write_filter,
    )


class ShoppingToolExecutor(BaseToolExecutor):
    """``inline_context`` is set by the paths that have no per-request context block
    (SDK, MCP): ``get_preferences`` then also carries the tier-one memory and the
    account context the Messages API runtime injects into the prompt."""

    fence = STOREFRONT_FENCE
    components = PRESENTATION_COMPONENTS
    displayed_text = "Displayed to the customer."
    unavailable_text = (
        "{name} is temporarily unavailable. Work with what you already have or let the "
        "customer know."
    )
    not_offered_text = "{detail} is not something this store offers; say so plainly."
    sold_out_text = (
        "Nothing was added: {detail}. Tell the customer, offer what the message names as "
        "available, and add that only once they choose it."
    )
    absent_text = "{name} is not something this store offers; say so plainly and do not suggest it."

    def __init__(
        self,
        *,
        backend: StorefrontBackend,
        config: ShoppingAgentConfig,
        skills: SkillRegistry,
        session: ShoppingSessionContext,
        state: ShoppingSessionState,
        memory: MemoryRuntime | None = None,
        extensions: Sequence[PresentationExtension] = (),
        inline_context: bool = False,
    ) -> None:
        self._inline_context = inline_context
        super().__init__(
            backend=backend,
            config=config,
            skills=skills,
            session=session,
            state=state,
            memory=memory or build_memory(config, None),
            extensions=extensions,
        )

    @property
    def memory_subject(self) -> str:
        return self._session.user_id

    def domain_error(self, error: Exception) -> ToolOutcome | None:
        # The message is backend text arriving outside the fence: sanitized and capped.
        detail = self._sanitize(str(error), 200)
        if isinstance(error, Unavailable):
            return ToolOutcome.error(self.sold_out_text.format(detail=detail or "unavailable"))
        if isinstance(error, NotOffered):
            return ToolOutcome.error(self.not_offered_text.format(detail=detail or "This"))
        return None

    def handlers(self) -> dict[str, Handler]:
        return {
            "search_products": self._search_products,
            "get_product_details": self._get_product_details,
            "get_cart": self._get_cart,
            "add_to_cart": self._add_to_cart,
            "update_cart_item": self._update_cart_item,
            "remove_from_cart": self._remove_from_cart,
            "get_preferences": self._get_preferences,
            "get_orders": self._get_orders,
            "get_order_status": self._get_order_status,
            "search_policies": self._search_policies,
            "get_fulfillment_options": self._get_fulfillment_options,
            # Quotes
            "get_quotes": self._get_quotes,
            "get_quote": self._get_quote,
            "create_quote": self._create_quote,
            "submit_quote": self._submit_quote,
            "update_quote_item": self._update_quote_item,
            "load_quote_to_cart": self._load_quote_to_cart,
            # Approvals
            "submit_for_approval": self._submit_for_approval,
            "get_approval_status": self._get_approval_status,
            "recall_approval_request": self._recall_approval_request,
            # Assets
            "get_assets": self._get_assets,
            "get_asset_details": self._get_asset_details,
            # Promotions
            "get_promotions": self._get_promotions,
            "apply_promotion": self._apply_promotion,
            # Subscriptions
            "get_subscriptions": self._get_subscriptions,
            "get_subscription_details": self._get_subscription_details,
            "renew_subscription": self._renew_subscription,
        }

    # -- catalog ---------------------------------------------------------------------

    async def _search_products(self, tool_input: dict[str, Any]) -> ToolOutcome:
        query = self._sanitize(tool_input.get("query", ""), 300)
        filters = (
            parse_argument(SearchFilters, tool_input["filters"])
            if tool_input.get("filters")
            else None
        )
        limit = self._search_limit(tool_input.get("limit"))
        products = await self._backend.search_products(self._session, query, filters, limit)
        self._state.remember_products(products)
        return ToolOutcome(search_result_text(query, products, self._config.max_fenced_chars))

    async def _get_product_details(self, tool_input: dict[str, Any]) -> ToolOutcome:
        product_id = str(tool_input.get("product_id", ""))
        details = await self._backend.get_product_details(self._session, product_id)
        if details is None:
            return ToolOutcome.error(f"No product with id {product_id}.")
        # The variants enter provenance with the record, so the cart takes their ids.
        self._state.remember_products([details, *details.variants])
        return self._fenced(product_details_payload(details))

    # -- cart --------------------------------------------------------------------------

    async def _get_cart(self, _: dict[str, Any]) -> ToolOutcome:
        return self._fenced(cart_payload(await self._backend.get_cart(self._session)))

    async def _add_to_cart(self, tool_input: dict[str, Any]) -> ToolOutcome:
        return await gated_add_to_cart(
            backend=self._backend,
            config=self._config,
            session=self._session,
            state=self._state,
            product_id=str(tool_input.get("product_id", "")),
            quantity=int(tool_input.get("quantity") or 1),
        )

    async def _update_cart_item(self, tool_input: dict[str, Any]) -> ToolOutcome:
        return await gated_update_cart_item(
            backend=self._backend,
            config=self._config,
            session=self._session,
            state=self._state,
            product_id=str(tool_input.get("product_id", "")),
            quantity=int(tool_input.get("quantity") or 1),
        )

    async def _remove_from_cart(self, tool_input: dict[str, Any]) -> ToolOutcome:
        return await gated_remove_from_cart(
            backend=self._backend,
            session=self._session,
            state=self._state,
            product_id=str(tool_input.get("product_id", "")),
        )

    # -- customer context, orders, policies, fulfillment ---------------------------------

    async def _get_preferences(self, _: dict[str, Any]) -> ToolOutcome:
        prefs = await self._backend.get_preferences(self._session)
        payload = prefs.model_dump(exclude_none=True)
        if self._inline_context:
            facts = await self._memory.tier_one(self._session.user_id)
            payload["saved_memory"] = [memory_fact_payload(f) for f in facts] or "none"
            account = await self._backend.get_account_context(self._session)
            if account is not None:
                payload["account"] = account
        return self._fenced(payload)

    async def _get_orders(self, tool_input: dict[str, Any]) -> ToolOutcome:
        limit = clamp_limit(tool_input.get("limit"), 5, MAX_ORDERS)
        orders = await self._backend.get_orders(self._session, limit)
        remember_order_items(self._state, orders)
        return self._fenced(orders_payload(orders))

    async def _get_order_status(self, tool_input: dict[str, Any]) -> ToolOutcome:
        order_id = str(tool_input.get("order_id", ""))
        order = await self._backend.get_order(self._session, order_id)
        if order is None:
            return ToolOutcome.error(f"No order with id {order_id}.")
        remember_order_items(self._state, [order])
        return self._fenced(order_payload(order))

    async def _search_policies(self, tool_input: dict[str, Any]) -> ToolOutcome:
        query = self._sanitize(tool_input.get("query", ""), 200)
        policies = await self._backend.search_policies(self._session, query)
        return self._fenced(policies_payload(policies))

    async def _get_fulfillment_options(self, tool_input: dict[str, Any]) -> ToolOutcome:
        product_ids = [str(pid) for pid in tool_input.get("product_ids") or []][
            :MAX_FULFILLMENT_IDS
        ]
        options = await self._backend.get_fulfillment_options(self._session, product_ids)
        return self._fenced(fulfillment_payload(options))

    # -- quotes -----------------------------------------------------------------------

    async def _get_quotes(self, _: dict[str, Any]) -> ToolOutcome:
        quotes = await self._backend.get_quotes(self._session)
        return self._fenced(quotes_payload(quotes))

    async def _get_quote(self, tool_input: dict[str, Any]) -> ToolOutcome:
        quote_id = str(tool_input.get("quote_id", ""))
        quote = await self._backend.get_quote(self._session, quote_id)
        if quote is None:
            return ToolOutcome.error(f"No quote with id {quote_id}.")
        return self._fenced(quote_payload(quote))

    async def _create_quote(self, tool_input: dict[str, Any]) -> ToolOutcome:
        name = tool_input.get("name")
        notes = tool_input.get("notes")
        quote = await self._backend.create_quote(self._session, name=name, notes=notes)
        return self._fenced(quote_payload(quote))

    async def _submit_quote(self, tool_input: dict[str, Any]) -> ToolOutcome:
        quote_id = str(tool_input.get("quote_id", ""))
        quote = await self._backend.submit_quote(self._session, quote_id)
        return self._fenced(quote_payload(quote))

    async def _update_quote_item(self, tool_input: dict[str, Any]) -> ToolOutcome:
        quote_id = str(tool_input.get("quote_id", ""))
        line_item_id = str(tool_input.get("line_item_id", ""))
        quantity = int(tool_input.get("quantity", 1))
        quote = await self._backend.update_quote_item(self._session, quote_id, line_item_id, quantity)
        return self._fenced(quote_payload(quote))

    async def _load_quote_to_cart(self, tool_input: dict[str, Any]) -> ToolOutcome:
        quote_id = str(tool_input.get("quote_id", ""))
        cart = await self._backend.load_quote_to_cart(self._session, quote_id)
        return self._fenced(cart_payload(cart))

    # -- approvals --------------------------------------------------------------------

    async def _submit_for_approval(self, tool_input: dict[str, Any]) -> ToolOutcome:
        subject_type = str(tool_input.get("subject_type", ""))
        subject_id = str(tool_input.get("subject_id", ""))
        request = await self._backend.submit_for_approval(
            self._session, subject_type, subject_id
        )
        return self._fenced(approval_payload(request))

    async def _get_approval_status(self, tool_input: dict[str, Any]) -> ToolOutcome:
        request_id = str(tool_input.get("request_id", ""))
        request = await self._backend.get_approval_status(self._session, request_id)
        if request is None:
            return ToolOutcome.error(f"No approval request with id {request_id}.")
        return self._fenced(approval_payload(request))

    async def _recall_approval_request(self, tool_input: dict[str, Any]) -> ToolOutcome:
        request_id = str(tool_input.get("request_id", ""))
        request = await self._backend.recall_approval_request(self._session, request_id)
        return self._fenced(approval_payload(request))

    # -- assets -----------------------------------------------------------------------

    async def _get_assets(self, tool_input: dict[str, Any]) -> ToolOutcome:
        assets = await self._backend.get_assets(
            self._session,
            category=tool_input.get("category"),
            status=tool_input.get("status"),
            query=tool_input.get("query"),
        )
        return self._fenced(assets_payload(assets))

    async def _get_asset_details(self, tool_input: dict[str, Any]) -> ToolOutcome:
        asset_id = str(tool_input.get("asset_id", ""))
        asset = await self._backend.get_asset_details(self._session, asset_id)
        if asset is None:
            return ToolOutcome.error(f"No asset with id {asset_id}.")
        return self._fenced(asset.model_dump(mode="json", exclude_none=True))

    # -- promotions -------------------------------------------------------------------

    async def _get_promotions(self, tool_input: dict[str, Any]) -> ToolOutcome:
        promotions = await self._backend.get_promotions(
            self._session, category=tool_input.get("category")
        )
        return self._fenced(promotions_payload(promotions))

    async def _apply_promotion(self, tool_input: dict[str, Any]) -> ToolOutcome:
        cart = await self._backend.apply_promotion(
            self._session,
            promotion_id=tool_input.get("promotion_id"),
            code=tool_input.get("code"),
        )
        return self._fenced(cart_payload(cart))

    # -- subscriptions ----------------------------------------------------------------

    async def _get_subscriptions(self, tool_input: dict[str, Any]) -> ToolOutcome:
        subscriptions = await self._backend.get_subscriptions(
            self._session, status=tool_input.get("status")
        )
        return self._fenced(subscriptions_payload(subscriptions))

    async def _get_subscription_details(self, tool_input: dict[str, Any]) -> ToolOutcome:
        subscription_id = str(tool_input.get("subscription_id", ""))
        sub = await self._backend.get_subscription_details(self._session, subscription_id)
        if sub is None:
            return ToolOutcome.error(f"No subscription with id {subscription_id}.")
        return self._fenced(sub.model_dump(mode="json", exclude_none=True))

    async def _renew_subscription(self, tool_input: dict[str, Any]) -> ToolOutcome:
        subscription_id = str(tool_input.get("subscription_id", ""))
        result = await self._backend.renew_subscription(self._session, subscription_id)
        return self._fenced(quote_payload(result))
