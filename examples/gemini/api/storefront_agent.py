"""Gemini ADK storefront (shopping) agent.

Builds a Google ADK Agent backed by the SFCC storefront tools and exposes a
``stream_turn`` async generator that yields SSE-formatted strings matching the
format expected by the storefront-web frontend.

SSE event types emitted:
  text_delta      {"text": "..."}
  tool_call       {"name": "...", "id": "...", "input": {...}}
  tool_result     {"name": "...", "id": "...", "content": "..."}
  cart_update     {"cart": {...}}
  turn_complete   {"stop_reason": "end_turn", "usage": {}, "elapsed_ms": N}
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are DreamHaus Assistant, a helpful shopping assistant for DreamHaus, \
a fine jewelry and accessories store.

You help shoppers discover products, compare options, manage their cart, and answer questions \
about jewelry, materials, sizing, and care.

## Catalog
DreamHaus sells rings, necklaces, bracelets, earrings, and accessories. Products are real \
items from the SFCC catalog — always search before recommending.

## Core rules
- Search the catalog before suggesting specific products. Never invent product IDs or prices.
- When adding to cart, first use search_products or get_product to confirm the product details.
- Be warm and helpful. Use jewelry expertise — mention materials, styles, and occasion suitability.
- Keep replies concise. Lead with the most relevant products or answer, not preamble.
- When the shopper asks for recommendations, search first, then present 2-4 options with prices.

## Cart behaviour
- After adding, removing, or updating cart items, always call get_cart to confirm the new state.
- Confirm what you added/removed before moving on.
- Never make up products. If a product_id is not in search results, search again first.

## Session
The shopper's session_id is available as context — use it for all cart operations.
"""


def _build_agent(store_name: str, session_id: str) -> Any:
    """Construct and return the ADK Agent instance."""
    try:
        from google.adk.agents import Agent
    except ImportError as exc:
        raise ImportError(
            "google-adk is required. Install it with: pip install google-adk"
        ) from exc

    from .storefront_tools import ALL_STOREFRONT_TOOLS
    import functools

    # Bind session_id into the cart tools so ADK can call them without it
    def _bound(fn: Any, **kw: Any) -> Any:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs.update(kw)
            return fn(*args, **kwargs)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs.update(kw)
            return await fn(*args, **kwargs)
        import inspect
        return async_wrapper if inspect.iscoroutinefunction(fn) else wrapper

    cart_tools = ["add_to_cart", "remove_from_cart", "update_cart_quantity", "get_cart"]
    tools = []
    for fn in ALL_STOREFRONT_TOOLS:
        if fn.__name__ in cart_tools:
            tools.append(_bound(fn, session_id=session_id))
        else:
            tools.append(fn)

    return Agent(
        name=f"gemini_storefront_{session_id[:8]}",
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        description=f"Shopping assistant for {store_name}",
        instruction=_SYSTEM_PROMPT,
        tools=tools,
    )


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class GeminiStorefrontAgent:
    """One deployment of the Gemini ADK storefront agent.

    Because cart tools need the session_id and ADK agents are not easily
    parameterised after construction, we create a fresh ADK agent per session
    (each with its own runner and session service) so the bound session_id is correct.
    """

    def __init__(self, store_name: str) -> None:
        self._store_name = store_name
        # Per-session runners: {session_id: (runner, session_service)}
        self._sessions: dict[str, Any] = {}

    def _get_or_create_runner(self, session_id: str) -> tuple[Any, Any]:
        if session_id not in self._sessions:
            try:
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService
            except ImportError as exc:
                raise ImportError(
                    "google-adk is required. Install it with: pip install google-adk"
                ) from exc

            svc = InMemorySessionService()
            runner = Runner(
                agent=_build_agent(self._store_name, session_id),
                app_name="gemini_storefront",
                session_service=svc,
            )
            self._sessions[session_id] = (runner, svc)
        return self._sessions[session_id]

    async def ensure_session(self, session_id: str) -> None:
        runner, svc = self._get_or_create_runner(session_id)
        try:
            existing = await svc.get_session(
                app_name="gemini_storefront",
                user_id="shopper",
                session_id=session_id,
            )
            if existing is None:
                await svc.create_session(
                    app_name="gemini_storefront",
                    user_id="shopper",
                    session_id=session_id,
                )
        except Exception:
            try:
                await svc.create_session(
                    app_name="gemini_storefront",
                    user_id="shopper",
                    session_id=session_id,
                )
            except Exception:
                pass

    def drop_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def stream_turn(
        self, session_id: str, user_message: str
    ) -> AsyncIterator[str]:
        """Run one agent turn and yield SSE frames."""
        from google.genai import types as gtypes
        from .storefront_tools import _cart_payload, _carts

        runner, _ = self._get_or_create_runner(session_id)
        await self.ensure_session(session_id)

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0
        cart_before = json.dumps(_cart_payload(session_id))

        try:
            async for event in runner.run_async(
                user_id="shopper",
                session_id=session_id,
                new_message=gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(text=user_message)],
                ),
            ):
                if hasattr(event, "usage_metadata") and event.usage_metadata:
                    um = event.usage_metadata
                    input_tokens = getattr(um, "prompt_token_count", input_tokens) or input_tokens
                    output_tokens = getattr(um, "candidates_token_count", output_tokens) or output_tokens

                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    if hasattr(part, "function_call") and part.function_call and part.function_call.name:
                        fc = part.function_call
                        yield _sse("tool_call", {
                            "name": fc.name,
                            "id": fc.name,
                            "input": dict(fc.args) if fc.args else {},
                        })

                    elif hasattr(part, "function_response") and part.function_response and part.function_response.name:
                        fr = part.function_response
                        result = fr.response or {}
                        yield _sse("tool_result", {
                            "name": fr.name,
                            "id": fr.name,
                            "content": json.dumps(result),
                        })
                        # Emit cart_update whenever a cart tool ran and the cart changed
                        if fr.name in ("add_to_cart", "remove_from_cart", "update_cart_quantity"):
                            cart_now = json.dumps(_cart_payload(session_id))
                            if cart_now != cart_before:
                                cart_before = cart_now
                                yield _sse("cart_update", {"cart": _cart_payload(session_id)})

                    elif hasattr(part, "text") and part.text:
                        yield _sse("text_delta", {"text": part.text})

        except Exception as exc:
            log.error("Gemini storefront turn error: %s", exc)
            yield _sse("text_delta", {"text": f"\n\n[Error: {exc}]"})

        elapsed = int((time.monotonic() - started) * 1000)
        yield _sse("turn_complete", {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "elapsed_ms": elapsed,
            "results_cleared": False,
        })
