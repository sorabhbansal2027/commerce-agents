#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Gemini AI Studio → UCP Commerce Bridge
=======================================
Connects your Google AI Studio Gemini model to the ACME storefront's
UCP (Universal Commerce Protocol) REST endpoints, enabling the full
browse → search → add-to-cart → checkout journey.

This is the execution layer that AI Studio's playground cannot run
automatically — AI Studio shows the function call; this script runs it.

Usage:
    pip install google-genai httpx
    GEMINI_API_KEY=your-key python scripts/gemini_aistudio_bridge.py

Env vars:
    GEMINI_API_KEY   required  — from aistudio.google.com/apikey
    UCP_BASE         optional  — defaults to Railway deployment
    GEMINI_MODEL     optional  — defaults to gemini-2.0-flash
"""

from __future__ import annotations

import os
import sys
import json
import httpx
from typing import Any

try:
    from google import genai
    from google.genai import types as gtypes
except ImportError:
    print("Install google-genai:  pip install google-genai httpx")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
UCP_BASE = os.environ.get("UCP_BASE", "https://diligent-flow-production-afd7.up.railway.app").rstrip("/")
MODEL    = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
API_KEY  = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("Error: set GEMINI_API_KEY environment variable")
    print("Get one at: https://aistudio.google.com/apikey")
    sys.exit(1)

# ── System instruction (matches AI Studio setup) ──────────────────────────────
SYSTEM_INSTRUCTION = """You are a shopping assistant for an ACME B2B/B2C commerce storefront.
You help users search for products, manage their cart, and create checkout sessions.

Available payment methods: credit_card, purchase_order (B2B).
Cart writes require a session — always call create_session first, then pass the session_id to add_to_cart.
Checkout sessions are pending confirmations — no charge is made. The buyer completes purchase.

When creating a checkout session, confirm items and total with the user first.
Format prices as USD currency. Keep responses concise and helpful."""

# ── Function declarations (identical to AI Studio tool config) ────────────────
TOOLS = [gtypes.Tool(function_declarations=[
    gtypes.FunctionDeclaration(
        name="search_products",
        description="Search the product catalog by keyword, category, or price range.",
        parameters={
            "type": "object",
            "properties": {
                "q":         {"type": "string",  "description": "Search query (keyword, brand, or description)"},
                "category":  {"type": "string",  "description": "Filter by product category"},
                "min_price": {"type": "number",  "description": "Minimum price filter"},
                "max_price": {"type": "number",  "description": "Maximum price filter"},
                "limit":     {"type": "integer", "description": "Max results (default 8, max 50)"},
            },
        },
    ),
    gtypes.FunctionDeclaration(
        name="get_product",
        description="Get full details for a specific product by its ID.",
        parameters={
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "The product_id from search results"},
            },
            "required": ["product_id"],
        },
    ),
    gtypes.FunctionDeclaration(
        name="create_session",
        description="Create a shopping session. Required before adding items to cart.",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Optional user identifier"},
            },
        },
    ),
    gtypes.FunctionDeclaration(
        name="add_to_cart",
        description="Add a product to the cart. Requires session_id from create_session.",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string",  "description": "Session ID from create_session"},
                "product_id": {"type": "string",  "description": "Product ID to add"},
                "quantity":   {"type": "integer", "description": "Quantity (default 1)"},
            },
            "required": ["session_id", "product_id"],
        },
    ),
    gtypes.FunctionDeclaration(
        name="create_checkout_session",
        description="Create a checkout session. No payment charged — stages the order.",
        parameters={
            "type": "object",
            "properties": {
                "line_items": {
                    "type": "array",
                    "description": "Items to checkout",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity":   {"type": "integer"},
                        },
                    },
                },
                "payment_handler": {"type": "string",  "description": "credit_card or purchase_order"},
                "buyer_name":      {"type": "string",  "description": "Buyer full name"},
                "buyer_email":     {"type": "string",  "description": "Buyer email address"},
            },
            "required": ["line_items"],
        },
    ),
])]

# ── UCP HTTP execution layer ──────────────────────────────────────────────────
# This is what AI Studio playground cannot do automatically.
# When Gemini decides to call a function, we execute the real HTTP request.

http = httpx.Client(timeout=15.0)
_session_id: str | None = None  # persisted across turns

def execute_function(name: str, args: dict[str, Any]) -> dict[str, Any]:
    global _session_id
    try:
        if name == "search_products":
            params: dict[str, Any] = {}
            if args.get("q"):          params["q"]         = args["q"]
            if args.get("category"):   params["category"]  = args["category"]
            if args.get("min_price"):  params["min_price"] = args["min_price"]
            if args.get("max_price"):  params["max_price"] = args["max_price"]
            if args.get("limit"):      params["limit"]     = min(int(args["limit"]), 50)
            r = http.get(f"{UCP_BASE}/ucp/products", params=params)
            r.raise_for_status()
            return r.json()

        elif name == "get_product":
            pid = args["product_id"]
            r = http.get(f"{UCP_BASE}/ucp/products/{pid}")
            r.raise_for_status()
            return r.json()

        elif name == "create_session":
            body = {}
            if args.get("user_id"):
                body["user_id"] = args["user_id"]
            r = http.post(f"{UCP_BASE}/ucp/sessions", json=body)
            r.raise_for_status()
            data = r.json()
            _session_id = data.get("session_id")
            return data

        elif name == "add_to_cart":
            sid = args.get("session_id") or _session_id
            body = {
                "product_id": args["product_id"],
                "quantity":   args.get("quantity", 1),
            }
            r = http.post(
                f"{UCP_BASE}/ucp/cart/items",
                headers={"X-Session-Id": sid} if sid else {},
                json=body,
            )
            r.raise_for_status()
            return r.json()

        elif name == "create_checkout_session":
            body = {
                "line_items":      args.get("line_items", []),
                "payment_handler": args.get("payment_handler", "credit_card"),
            }
            if args.get("buyer_name") or args.get("buyer_email"):
                body["buyer"] = {k: v for k, v in {
                    "name":  args.get("buyer_name"),
                    "email": args.get("buyer_email"),
                }.items() if v}
            r = http.post(f"{UCP_BASE}/ucp/checkout-sessions", json=body)
            r.raise_for_status()
            return r.json()

        else:
            return {"error": f"Unknown function: {name}"}

    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}", "detail": e.response.text}
    except Exception as e:
        return {"error": str(e)}

# ── Agentic loop (same pattern as AI Studio "Get Code" export) ────────────────

client = genai.Client(api_key=API_KEY)
config = gtypes.GenerateContentConfig(tools=TOOLS, system_instruction=SYSTEM_INSTRUCTION)
contents: list[gtypes.Content] = []

def chat(message: str) -> str:
    contents.append(gtypes.Content(role="user", parts=[gtypes.Part(text=message)]))

    while True:
        response = client.models.generate_content(
            model=MODEL, contents=contents, config=config
        )
        candidate = response.candidates[0]
        contents.append(candidate.content)

        fn_calls = [p.function_call for p in candidate.content.parts
                    if p.function_call and p.function_call.name]

        if not fn_calls:
            return "\n".join(
                p.text for p in candidate.content.parts
                if hasattr(p, "text") and p.text
            ).strip()

        # Execute every function call and feed results back
        fn_parts: list[gtypes.Part] = []
        for fc in fn_calls:
            print(f"  → {fc.name}({json.dumps(dict(fc.args), separators=(',', ':'))})")
            result = execute_function(fc.name, dict(fc.args))
            fn_parts.append(gtypes.Part(
                function_response=gtypes.FunctionResponse(
                    name=fc.name, response={"result": result}
                )
            ))

        contents.append(gtypes.Content(role="user", parts=fn_parts))


# ── Interactive REPL ──────────────────────────────────────────────────────────

def main() -> None:
    print(f"Gemini × UCP Commerce  [{MODEL}]")
    print(f"Store: {UCP_BASE}")
    print("Type your message, or 'reset' / 'quit'.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "reset":
            contents.clear()
            print("Conversation reset.\n")
            continue

        reply = chat(user_input)
        print(f"\nGemini: {reply}\n")


if __name__ == "__main__":
    main()
