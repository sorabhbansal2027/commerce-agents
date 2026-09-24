# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Gemini × UCP integration: a Google Gemini agent that auto-discovers a storefront
via its UCP manifest and shops through the standard REST endpoints.

The agent fetches ``GET /.well-known/ucp`` at startup, reads the capability manifest,
and builds Gemini function declarations dynamically — no store-specific code required.
Any UCP-compliant storefront works without modification.

Usage::

    from demo_common.gemini_ucp_agent import GeminiUCPAgent

    agent = GeminiUCPAgent(ucp_base="https://your-store.example.com")
    print(agent.send("Find laptops under $1500 and create a checkout session for one"))

Requires ``google-genai`` (``pip install google-genai``) and ``GEMINI_API_KEY`` in
the environment.
"""

from __future__ import annotations

import importlib.util as _ilu
import os
from pathlib import Path
from typing import Any

import httpx

# Import ontology from the same package directory without requiring an installed package.
_onto_path = Path(__file__).with_name("ontology.py")
_onto_spec = _ilu.spec_from_file_location("_ontology", _onto_path)
_onto_mod = _ilu.module_from_spec(_onto_spec)  # type: ignore[arg-type]
_onto_spec.loader.exec_module(_onto_mod)  # type: ignore[union-attr]
_expand_query = _onto_mod.expand_query
_get_agent_hints = _onto_mod.get_agent_hints

try:
    from google import genai
    from google.genai import types as gtypes
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "google-genai is required for Gemini UCP integration. "
        "Install it with: pip install google-genai"
    ) from _e


_DEFAULT_MODEL = "gemini-3.6-flash"
_TIMEOUT = 15.0


class GeminiUCPAgent:
    """A Gemini agent that shops via UCP REST endpoints it discovers at runtime.

    The constructor fetches the UCP manifest, builds Gemini function declarations
    from the manifest's ``capabilities`` block, and initialises the Gemini client.
    All subsequent ``send()`` calls run a full agentic loop: Gemini decides which
    UCP endpoints to call, the agent executes the HTTP requests, and the results
    are fed back until Gemini produces a final text response.
    """

    def __init__(
        self,
        ucp_base: str,
        *,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        self.base = ucp_base.rstrip("/")
        self._http = httpx.Client(timeout=_TIMEOUT)
        self._model_name = model

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is required for Gemini UCP integration"
            )
        self._client = genai.Client(api_key=key)

        # Auto-discover capabilities from the UCP manifest
        resp = self._http.get(f"{self.base}/.well-known/ucp")
        resp.raise_for_status()
        self.manifest: dict[str, Any] = resp.json()
        self._tools = self._build_tools()
        self._config = gtypes.GenerateContentConfig(
            tools=self._tools,
            system_instruction=self._system_prompt(),
        )
        # Conversation history (stateful)
        self._contents: list[gtypes.Content] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def store_name(self) -> str:
        return self.manifest.get("business", {}).get("name", "the store")

    @property
    def tool_names(self) -> list[str]:
        return [
            fn.name
            for tool in self._tools
            for fn in (tool.function_declarations or [])
        ]

    def send(self, message: str) -> str:
        """Send a user message and return Gemini's final text response.

        Handles the full agentic loop: Gemini may call UCP endpoints multiple
        times before producing a response; each function call is executed and
        returned as a function_response before the loop continues.
        """
        self._contents.append(
            gtypes.Content(role="user", parts=[gtypes.Part(text=message)])
        )

        while True:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=self._contents,
                config=self._config,
            )
            candidate = response.candidates[0]
            self._contents.append(candidate.content)

            fn_calls = [
                part.function_call
                for part in candidate.content.parts
                if part.function_call and part.function_call.name
            ]

            if not fn_calls:
                # No more tool calls — collect all text parts as the answer
                return "\n".join(
                    part.text
                    for part in candidate.content.parts
                    if hasattr(part, "text") and part.text
                ).strip()

            # Execute every function call and feed results back.
            # google-genai requires role="user" for function_response parts.
            fn_response_parts: list[gtypes.Part] = []
            for fc in fn_calls:
                result = self._call_ucp(fc.name, dict(fc.args))
                fn_response_parts.append(
                    gtypes.Part(
                        function_response=gtypes.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

            self._contents.append(
                gtypes.Content(role="user", parts=fn_response_parts)
            )

    def reset(self) -> None:
        """Clear conversation history (start a fresh session)."""
        self._contents = []

    # ------------------------------------------------------------------
    # Internal: UCP HTTP calls
    # ------------------------------------------------------------------

    def _call_ucp(self, fn_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Route a Gemini function call to the appropriate UCP REST endpoint."""
        args = _expand_query(fn_name, args)
        try:
            if fn_name == "search_products":
                params: dict[str, Any] = {}
                if args.get("q"):
                    params["q"] = args["q"]
                if args.get("category"):
                    params["category"] = args["category"]
                if args.get("max_price") is not None:
                    params["max_price"] = args["max_price"]
                if args.get("min_price") is not None:
                    params["min_price"] = args["min_price"]
                if args.get("limit"):
                    params["limit"] = min(int(args["limit"]), 50)
                r = self._http.get(f"{self.base}/ucp/products", params=params)
                r.raise_for_status()
                return r.json()

            elif fn_name == "get_product":
                pid = args.get("product_id", "")
                r = self._http.get(f"{self.base}/ucp/products/{pid}")
                r.raise_for_status()
                return r.json()

            elif fn_name == "create_checkout_session":
                body: dict[str, Any] = {
                    "line_items": args.get("line_items", []),
                    "payment_handler": args.get("payment_handler", "credit_card"),
                }
                if args.get("buyer_name") or args.get("buyer_email"):
                    body["buyer"] = {
                        k: v
                        for k, v in {
                            "name": args.get("buyer_name"),
                            "email": args.get("buyer_email"),
                        }.items()
                        if v
                    }
                r = self._http.post(f"{self.base}/ucp/checkout-sessions", json=body)
                r.raise_for_status()
                return r.json()

            else:
                return {"error": f"Unknown UCP function: {fn_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}", "detail": exc.response.text}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Internal: manifest → Gemini tool declarations
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        biz = self.manifest.get("business", {})
        hints = self.manifest.get("agent_hints", "")
        caps = self.manifest.get("capabilities", {})

        payment_ids = ", ".join(
            p["id"] for p in self.manifest.get("payment_handlers", [])
        )

        cart_note = ""
        if caps.get("cart_management", {}).get("note"):
            cart_note = caps["cart_management"]["note"]

        checkout_note = ""
        if caps.get("checkout", {}).get("note"):
            checkout_note = caps["checkout"]["note"]

        return (
            f"You are a shopping assistant for {biz.get('name', 'this store')}. "
            f"{biz.get('description', '')} "
            f"Available payment methods: {payment_ids or 'credit_card'}. "
            f"{cart_note} "
            f"{checkout_note} "
            f"{hints} "
            f"{_get_agent_hints()} "
            "When creating a checkout session, always confirm the items and total with the user first. "
            "Format prices as currency. Keep responses concise and helpful."
        )

    def _build_tools(self) -> list[gtypes.Tool]:
        caps = self.manifest.get("capabilities", {})
        tools: list[gtypes.Tool] = []

        # ── Product discovery ──────────────────────────────────────────
        if caps.get("product_discovery", {}).get("supported"):
            features = caps["product_discovery"].get("features", [])
            search_desc = (
                "Search the product catalog. "
                + ("Supports text search, " if "full_text_search" in features else "")
                + ("category filter, " if "category_filter" in features else "")
                + ("price range filter, " if "price_range_filter" in features else "")
                + ("availability filter." if "availability_filter" in features else "")
            ).rstrip(", .")

            tools.append(
                gtypes.Tool(
                    function_declarations=[
                        gtypes.FunctionDeclaration(
                            name="search_products",
                            description=search_desc,
                            parameters={
                                "type": "object",
                                "properties": {
                                    "q": {
                                        "type": "string",
                                        "description": "Search query (keyword, brand, or description)",
                                    },
                                    "category": {
                                        "type": "string",
                                        "description": "Filter by product category",
                                    },
                                    "min_price": {
                                        "type": "number",
                                        "description": "Minimum price filter",
                                    },
                                    "max_price": {
                                        "type": "number",
                                        "description": "Maximum price filter",
                                    },
                                    "limit": {
                                        "type": "integer",
                                        "description": "Number of results to return (1-50, default 8)",
                                    },
                                },
                            },
                        ),
                        gtypes.FunctionDeclaration(
                            name="get_product",
                            description="Get full details for a specific product by its ID.",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "product_id": {
                                        "type": "string",
                                        "description": "The product_id returned by search_products",
                                    },
                                },
                                "required": ["product_id"],
                            },
                        ),
                    ]
                )
            )

        # ── Checkout ───────────────────────────────────────────────────
        if caps.get("checkout", {}).get("supported"):
            payment_ids = [p["id"] for p in self.manifest.get("payment_handlers", [])]
            ph_desc = (
                f"Payment handler to use. Options: {', '.join(payment_ids)}."
                if payment_ids
                else "Payment handler identifier."
            )
            tools.append(
                gtypes.Tool(
                    function_declarations=[
                        gtypes.FunctionDeclaration(
                            name="create_checkout_session",
                            description=(
                                "Create a pending checkout session from a list of products. "
                                "No payment is charged — this is a confirmation pending buyer approval. "
                                "Always confirm items and total with the user before calling this."
                            ),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "line_items": {
                                        "type": "array",
                                        "description": "Products to purchase",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "product_id": {"type": "string"},
                                                "quantity": {
                                                    "type": "integer",
                                                    "description": "Quantity (default 1)",
                                                },
                                            },
                                            "required": ["product_id"],
                                        },
                                    },
                                    "payment_handler": {
                                        "type": "string",
                                        "description": ph_desc,
                                    },
                                    "buyer_name": {
                                        "type": "string",
                                        "description": "Buyer's full name",
                                    },
                                    "buyer_email": {
                                        "type": "string",
                                        "description": "Buyer's email address",
                                    },
                                },
                                "required": ["line_items"],
                            },
                        ),
                    ]
                )
            )

        return tools
