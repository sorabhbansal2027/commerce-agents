"""Gemini ADK merchant agent.

Builds a Google ADK Agent backed by the SFCC merchant tools, exposes a
``stream_turn`` async generator that yields SSE-formatted strings in the same
format as the Anthropic-based agent so the existing merchant-web frontend works
without modification.

SSE event types emitted:
  text_delta      {"text": "..."}
  tool_call       {"name": "...", "id": "...", "input": {...}}
  tool_result     {"name": "...", "id": "...", "content": "..."}
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

_SYSTEM_PROMPT = """You are a merchant AI assistant for {store_name}, a commerce store.

You help the merchant operator understand their business, manage their catalog, \
monitor inventory, and take action on orders and product listings.

## Core rules
- Always read data before writing (get_listing before stage_listing_update, \
get_inventory_alerts before stage_inventory_action).
- Stage changes first; never apply without the operator's explicit approval.
- Be concise and direct. Lead with figures and recommendations, not preamble.
- When inventory alerts show low stock, proactively suggest restocking actions.
- Present numbers with units (currency, units, percentages).

## Workflow
1. Greet the operator briefly on first message and offer a snapshot or briefing.
2. For any question about business performance, call get_business_snapshot first.
3. For inventory questions, call get_inventory_alerts.
4. For catalog work, search first then get the full listing before editing.
5. After staging a change, tell the operator what was staged and ask for approval.
6. Only call apply_change after the operator explicitly says "approve" or "apply".
"""


def _build_agent(store_name: str) -> Any:
    """Construct and return the ADK Agent instance."""
    try:
        from google.adk.agents import Agent
    except ImportError as exc:
        raise ImportError(
            "google-adk is required. Install it with: pip install google-adk"
        ) from exc

    from .tools import ALL_TOOLS

    return Agent(
        name="gemini_merchant_agent",
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        description=f"Merchant AI assistant for {store_name}",
        instruction=_SYSTEM_PROMPT.format(store_name=store_name),
        tools=ALL_TOOLS,
    )


def _sse(event_type: str, data: dict[str, Any]) -> str:
    """Format one SSE frame."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class GeminiMerchantAgent:
    """One deployment of the Gemini ADK merchant agent.

    ``runner`` and ``session_service`` are created once at startup and shared
    across all requests; ADK's InMemorySessionService maintains per-session
    conversation history.
    """

    def __init__(self, store_name: str) -> None:
        try:
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
        except ImportError as exc:
            raise ImportError(
                "google-adk is required. Install it with: pip install google-adk"
            ) from exc

        self._store_name = store_name
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=_build_agent(store_name),
            app_name="gemini_merchant",
            session_service=self._session_service,
        )

    async def ensure_session(self, session_id: str) -> None:
        """Create the ADK session if it does not exist yet."""
        try:
            from google.adk.sessions import InMemorySessionService

            existing = await self._session_service.get_session(
                app_name="gemini_merchant",
                user_id="operator",
                session_id=session_id,
            )
            if existing is None:
                await self._session_service.create_session(
                    app_name="gemini_merchant",
                    user_id="operator",
                    session_id=session_id,
                )
        except Exception:
            # ADK may raise if session doesn't exist; create it
            try:
                await self._session_service.create_session(
                    app_name="gemini_merchant",
                    user_id="operator",
                    session_id=session_id,
                )
            except Exception:
                pass

    async def stream_turn(
        self, session_id: str, user_message: str
    ) -> AsyncIterator[str]:
        """Run one agent turn and yield SSE frames.

        Yields text_delta frames while the model streams text, tool_call /
        tool_result frames around each function execution, and a final
        turn_complete frame.
        """
        from google.genai import types as gtypes

        await self.ensure_session(session_id)

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        try:
            async for event in self._runner.run_async(
                user_id="operator",
                session_id=session_id,
                new_message=gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(text=user_message)],
                ),
            ):
                # Accumulate usage when available
                if hasattr(event, "usage_metadata") and event.usage_metadata:
                    um = event.usage_metadata
                    input_tokens = getattr(um, "prompt_token_count", input_tokens) or input_tokens
                    output_tokens = getattr(um, "candidates_token_count", output_tokens) or output_tokens

                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    # Function call emitted by the model
                    if hasattr(part, "function_call") and part.function_call and part.function_call.name:
                        fc = part.function_call
                        yield _sse("tool_call", {
                            "name": fc.name,
                            "id": fc.name,
                            "input": dict(fc.args) if fc.args else {},
                        })

                    # Function response (result of tool execution)
                    elif hasattr(part, "function_response") and part.function_response and part.function_response.name:
                        fr = part.function_response
                        result = fr.response or {}
                        yield _sse("tool_result", {
                            "name": fr.name,
                            "id": fr.name,
                            "content": json.dumps(result),
                        })

                    # Text from the model
                    elif hasattr(part, "text") and part.text:
                        yield _sse("text_delta", {"text": part.text})

        except Exception as exc:
            log.error("Gemini agent turn error: %s", exc)
            yield _sse("text_delta", {"text": f"\n\n[Error: {exc}]"})

        elapsed = int((time.monotonic() - started) * 1000)
        yield _sse("turn_complete", {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "elapsed_ms": elapsed,
            "results_cleared": False,
        })
