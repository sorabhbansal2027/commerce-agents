"""Salesforce vertical agent configs — shopping + merchant, both reading from Salesforce OMS."""

from __future__ import annotations

from demo_common import host_approval_default, model_overrides
from merchant_agent import MerchantAgentConfig
from shopping_agent import ShoppingAgentConfig


def build_shopping_config() -> ShoppingAgentConfig:
    return ShoppingAgentConfig(
        brand_name="ACME",
        assistant_name="ACME Assistant",
        brand_voice="professional, warm, and brief",
        **model_overrides(model="SHOPPING_MODEL", memory_model="MEMORY_MODEL"),
    )


def build_merchant_config(store_name: str) -> MerchantAgentConfig:
    return MerchantAgentConfig(
        brand_name=store_name,
        require_host_approval=host_approval_default(),
        approval_surface="the Approve button on the change preview card",
        enable_analysis=False,
        thinking_effort=None,
        metrics_intent_terms=(
            "sales", "revenue", "orders", "traffic", "conversion", "aov",
            "average order value", "performance", "performing", "trend",
            "trending", "growth", "drop", "dropped", "spike", "returns rate",
            "return rate", "margin", "margins", "profit", "best seller",
            "best sellers", "slow mover", "slow movers", "sell-through",
            "campaign performance", "ad spend", "return on ad spend", "roas",
            "profile", "profiles", "platform team", "platform", "account",
            "created by", "ets profile", "etsprofile", "order summary",
            "ordersummary", "total orders", "total revenue",
        ),
        **model_overrides(model="MERCHANT_MODEL", memory_model="MEMORY_MODEL"),
    )
