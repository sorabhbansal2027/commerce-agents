"""Manual agentic loop using the raw google.genai Client.

Unlike the ADK path (examples/gemini/api/agent.py), there is no framework
managing the conversation: this module owns the full loop — building message
history, dispatching tool calls, running safety gates, updating provenance
tracking, and emitting SSE frames.

SSE event types emitted (same schema as the ADK path):
    text_delta      {"text": "..."}
    tool_call       {"name": "...", "id": "...", "input": {...}}
    tool_result     {"name": "...", "id": "...", "content": "..."}
    turn_complete   {"stop_reason": "end_turn", "usage": {}, "elapsed_ms": N}
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# examples/ must be on sys.path so gemini.api.* can be imported by name.
_examples_dir = str(Path(__file__).parent.parent.parent)
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

from gemini.api.tools import ALL_TOOL_FUNCTIONS, get_tool_declarations  # noqa: E402
from gates import run_gates  # noqa: E402  # gates.py is in the same directory

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a merchant AI assistant for {store_name}, a commerce store.

You help the merchant operator understand their business, manage their catalog,
monitor inventory, and take action on orders and product listings.

Today is {today}.

## Core rules
- Always read data before writing: call get_listing before stage_listing_update,
  call get_inventory_alerts before stage_inventory_action.
- Stage changes first; never apply without the operator's explicit approval.
- When the operator approves a change, call approve_change with the change_id,
  then call apply_change. Both calls are required.
- Be concise and direct. Lead with figures and recommendations, not preamble.
- Present numbers with units (currency, units, percentages).

## Workflow
1. For business performance questions, call get_business_snapshot first.
2. For inventory questions, call get_inventory_alerts.
3. For catalog work, search first, then get the full listing before editing.
4. After staging a change, tell the operator what was staged and ask for approval.
5. Only call approve_change then apply_change after the operator explicitly says
   "approve" or "apply".
"""


@dataclass
class GenAISession:
    session_id: str
    store_name: str
    messages: list = field(default_factory=list)
    # Populated by get_listing / search_listings results; required before writes.
    seen_listing_ids: set[str] = field(default_factory=set)
    # Populated when a stage_ tool succeeds and returns a change_id.
    seen_change_ids: set[str] = field(default_factory=set)
    # Populated when approve_change succeeds; required before apply_change.
    approved_change_ids: set[str] = field(default_factory=set)


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class GenAIMerchantOrchestrator:
    """Manual agentic loop over google.genai.Client for the merchant domain.

    One instance is created at startup and shared across requests; per-session
    conversation history and provenance state live in ``GenAISession`` objects
    kept in ``_sessions``.
    """

    def __init__(self, store_name: str) -> None:
        self._store_name = store_name
        self._sessions: dict[str, GenAISession] = {}
        # Defer client construction until the first request so GOOGLE_API_KEY
        # can be set during the lifespan startup hook before this runs.
        self._client: Any = None
        self._model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai  # type: ignore[import]

            api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def ensure_session(self, session_id: str) -> GenAISession:
        if session_id not in self._sessions:
            self._sessions[session_id] = GenAISession(
                session_id=session_id,
                store_name=self._store_name,
            )
        return self._sessions[session_id]

    def _update_provenance(
        self, session: GenAISession, tool_name: str, args: dict, result: Any
    ) -> None:
        if not isinstance(result, dict):
            return
        if tool_name == "get_listing":
            if lid := result.get("id"):
                session.seen_listing_ids.add(str(lid))
        elif tool_name == "search_listings":
            for listing in result.get("listings", []):
                if lid := listing.get("id"):
                    session.seen_listing_ids.add(str(lid))
        elif tool_name in ("stage_listing_update", "stage_inventory_action"):
            if cid := result.get("change_id"):
                session.seen_change_ids.add(str(cid))
        elif tool_name == "approve_change":
            if cid := result.get("approved"):
                session.approved_change_ids.add(str(cid))

    async def stream_turn(
        self, session_id: str, user_message: str
    ) -> AsyncIterator[str]:
        """Run one agentic turn and yield SSE frames until the model stops calling tools."""
        from google.genai import types as gtypes  # type: ignore[import]

        session = self.ensure_session(session_id)
        session.messages.append(
            gtypes.Content(role="user", parts=[gtypes.Part(text=user_message)])
        )

        today = datetime.date.today().isoformat()
        system_instruction = _SYSTEM_PROMPT.format(
            store_name=session.store_name, today=today
        )

        client = self._get_client()
        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        try:
            while True:
                response = await client.aio.models.generate_content(
                    model=self._model,
                    contents=session.messages,
                    config=gtypes.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=get_tool_declarations(),
                        temperature=0.3,
                    ),
                )

                if response.usage_metadata:
                    um = response.usage_metadata
                    input_tokens = getattr(um, "prompt_token_count", input_tokens) or input_tokens
                    output_tokens = (
                        getattr(um, "candidates_token_count", output_tokens) or output_tokens
                    )

                candidate = response.candidates[0]
                model_content = candidate.content
                function_calls = [
                    p.function_call
                    for p in model_content.parts
                    if p.function_call and p.function_call.name
                ]

                if not function_calls:
                    session.messages.append(model_content)
                    for part in model_content.parts:
                        if part.text:
                            yield _sse("text_delta", {"text": part.text})
                    break

                session.messages.append(model_content)
                function_response_parts: list[Any] = []

                for fc in function_calls:
                    args = dict(fc.args) if fc.args else {}
                    yield _sse("tool_call", {"name": fc.name, "id": fc.name, "input": args})

                    gate_error = run_gates(session, fc.name, args)
                    if gate_error:
                        result: dict = gate_error
                    else:
                        func = ALL_TOOL_FUNCTIONS.get(fc.name)
                        if func is None:
                            result = {"error": f"unknown tool: {fc.name!r}"}
                        else:
                            result = await func(**args)
                        self._update_provenance(session, fc.name, args, result)

                    yield _sse(
                        "tool_result",
                        {"name": fc.name, "id": fc.name, "content": json.dumps(result)},
                    )
                    function_response_parts.append(
                        gtypes.Part(
                            function_response=gtypes.FunctionResponse(
                                name=fc.name, response=result
                            )
                        )
                    )

                session.messages.append(
                    gtypes.Content(role="user", parts=function_response_parts)
                )

        except Exception as exc:
            log.error("GenAI orchestrator turn error: %s", exc)
            yield _sse("text_delta", {"text": f"\n\n[Error: {exc}]"})

        elapsed = int((time.monotonic() - started) * 1000)
        yield _sse(
            "turn_complete",
            {
                "stop_reason": "end_turn",
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                "elapsed_ms": elapsed,
            },
        )
