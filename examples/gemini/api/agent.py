"""Gemini ADK merchant agent — maximizing all ADK framework features.

ADK features used:
  • before_model_callback  — injects live date and store name into every model call
  • after_tool_callback    — caches key tool outputs in session.state; trims large arrays
  • sub_agents             — routes content tasks to a specialized MerchandisingAgent
  • session.state          — persists snapshot / alert data across turns
  • prime_session()        — proactively fetches a business briefing on login

SSE event types emitted:
  text_delta      {"text": "..."}
  tool_call       {"name": "...", "id": "...", "input": {...}}
  tool_result     {"name": "...", "id": "...", "content": "..."}
  turn_complete   {"stop_reason": "end_turn", "usage": {}, "elapsed_ms": N}
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _load_skills() -> str:
    """Load per-domain skill instructions from examples/gemini/skills/."""
    if not _SKILLS_DIR.exists():
        return ""
    parts = []
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        content = skill_md.read_text().strip()
        if content:
            parts.append(content)
    return "\n\n---\n\n".join(parts) if parts else ""

# ── System prompts ─────────────────────────────────────────────────────────────

_ORCHESTRATOR_PROMPT = """You are a merchant AI assistant for {store_name}, a commerce store.

You help the merchant operator understand their business, manage their catalog,
monitor inventory, and take action on orders and product listings.

## Core rules
- Always read data before writing (get_listing before stage_listing_update,
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

## Merchandising tasks
For all product content operations — enriching descriptions, generating SEO copy,
creating geo-targeted variants, or classifying products into the B2C taxonomy —
transfer to the merchandising_agent. That specialist handles the full
classify → enrich → stage workflow with dedicated content tools.
"""

_MERCH_PROMPT = """You are a merchandising specialist AI for {store_name}.

You handle all product content tasks: enriching descriptions, generating SEO copy,
creating geo-targeted variants, and classifying products into the B2C taxonomy.

## Rules
- Start by reading the product with get_listing (or search_listings if no ID is given).
- After enriching, offer to stage the changes with stage_listing_update.
- Only stage changes; never apply without explicit operator approval.
- Ground all copy in the product's actual attributes — do not invent specifications.
- Use classify_product_ontology before SEO or geo work to establish the product's taxonomy.
- For any ingredient or material, call lookup_product_entities to ground claims in facts.
"""


# ── ADK callbacks ──────────────────────────────────────────────────────────────


def _before_model_cb(callback_context: Any, llm_request: Any) -> None:
    """Inject live date and store name into every model call via system instruction."""
    today = datetime.date.today().isoformat()
    try:
        store = callback_context.state.get("store_name", "the store")
    except Exception:
        store = "the store"
    injection = f" [Context — Today: {today} | Store: {store}]"

    try:
        cfg = llm_request.config
        if cfg is None:
            return None
        si = cfg.system_instruction
        if si is None:
            return None
        if hasattr(si, "parts") and si.parts:
            for part in si.parts:
                if hasattr(part, "text") and isinstance(part.text, str):
                    part.text = part.text + injection
                    break
        elif isinstance(si, str):
            cfg.system_instruction = si + injection
    except Exception as exc:
        log.debug("before_model_cb: could not inject context: %s", exc)
    return None


_WRITE_TOOLS = {"stage_listing_update", "stage_inventory_action"}
_APPLY_TOOLS = {"apply_change"}


def _before_tool_cb(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    """Safety gates: provenance and approval — run before every tool call.

    Returns a dict to short-circuit the tool (dict becomes the result),
    or None to let the tool execute normally.

    PROVENANCE gate: write tools may only target listing_ids that were
    returned by a read tool (search_listings / get_listing) this session.
    Tracks seen IDs via session.state["seen_listing_ids"].

    APPROVAL gate: apply_change may only proceed if the operator called
    approve_change for that change_id this session.
    Tracks approved IDs via session.state["approved_change_ids"].
    """
    tool_name: str = getattr(tool, "name", None) or ""
    try:
        # Provenance gate
        if tool_name in _WRITE_TOOLS:
            listing_id = args.get("listing_id", "")
            seen: list[str] = tool_context.state.get("seen_listing_ids", [])
            if listing_id and listing_id not in seen:
                return {
                    "error": (
                        f"Provenance gate blocked: '{listing_id}' was not returned by "
                        "search_listings or get_listing this session. "
                        "Read the listing first before staging a change."
                    ),
                    "gate": "provenance",
                }

        # Options gate
        if tool_name == "stage_inventory_action":
            action = args.get("action", "")
            if action not in ("restock", "pause", "activate"):
                return {
                    "error": f"Options gate blocked: action '{action}' is invalid. Choose restock, pause, or activate.",
                    "gate": "options",
                }

        # Guardrail gate
        if tool_name == "stage_inventory_action":
            qty = args.get("quantity", 0)
            if isinstance(qty, int) and qty > 10_000:
                return {
                    "error": f"Guardrail gate blocked: quantity {qty} exceeds the per-action cap of 10 000 units.",
                    "gate": "guardrail",
                }

        # Approval gate
        if tool_name in _APPLY_TOOLS:
            change_id = args.get("change_id", "")
            approved: list[str] = tool_context.state.get("approved_change_ids", [])
            if change_id and change_id not in approved:
                return {
                    "error": (
                        f"Approval gate blocked: change '{change_id}' has not been "
                        "approved by the operator. "
                        "Call approve_change after the operator explicitly approves."
                    ),
                    "gate": "approval",
                }
    except Exception as exc:
        log.debug("before_tool_cb: gate check skipped: %s", exc)
    return None


def _after_tool_cb(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    tool_response: dict[str, Any],
) -> dict[str, Any] | None:
    """Cache key tool outputs in session.state and trim oversized payloads.

    Returns None to keep the original response, or a replacement dict to
    override it (used to trim large listing arrays before they hit the model).
    """
    tool_name: str = getattr(tool, "name", None) or ""
    now = datetime.datetime.utcnow().isoformat() + "Z"

    try:
        # Business snapshot cache
        if tool_name == "get_business_snapshot":
            tool_context.state["last_snapshot"] = tool_response
            tool_context.state["snapshot_fetched_at"] = now

        # Inventory alert cache
        elif tool_name == "get_inventory_alerts" and isinstance(tool_response, dict):
            alerts = tool_response.get("alerts", [])
            tool_context.state["last_alerts_count"] = len(alerts)
            tool_context.state["last_alerts_fetched_at"] = now

        # Provenance tracking — record IDs returned by read tools
        elif tool_name == "get_listing" and isinstance(tool_response, dict):
            lid = tool_response.get("id") or args.get("listing_id", "")
            if lid:
                seen: list[str] = list(tool_context.state.get("seen_listing_ids", []))
                if lid not in seen:
                    seen.append(lid)
                    tool_context.state["seen_listing_ids"] = seen

        elif tool_name == "search_listings" and isinstance(tool_response, dict):
            new_ids = [item.get("id") for item in tool_response.get("listings", []) if item.get("id")]
            seen = list(tool_context.state.get("seen_listing_ids", []))
            seen.extend(i for i in new_ids if i not in seen)
            tool_context.state["seen_listing_ids"] = seen

        # Stage tracking — record change IDs for approval gate
        elif tool_name in _WRITE_TOOLS and isinstance(tool_response, dict):
            cid = tool_response.get("change_id")
            if cid:
                staged: list[str] = list(tool_context.state.get("staged_change_ids", []))
                if cid not in staged:
                    staged.append(cid)
                    tool_context.state["staged_change_ids"] = staged

        elif tool_name == "get_pending_changes" and isinstance(tool_response, dict):
            new_cids = [c.get("change_id") for c in tool_response.get("changes", []) if c.get("change_id")]
            staged = list(tool_context.state.get("staged_change_ids", []))
            staged.extend(c for c in new_cids if c not in staged)
            tool_context.state["staged_change_ids"] = staged

        # Approval tracking — approve_change records the change_id
        elif tool_name == "approve_change" and isinstance(tool_response, dict):
            cid = tool_response.get("approved")
            if cid:
                approved: list[str] = list(tool_context.state.get("approved_change_ids", []))
                if cid not in approved:
                    approved.append(cid)
                    tool_context.state["approved_change_ids"] = approved

    except Exception as exc:
        log.debug("after_tool_cb: session state write skipped: %s", exc)

    # Trim oversized listing arrays to keep context window manageable
    if tool_name == "search_listings" and isinstance(tool_response, dict):
        listings = tool_response.get("listings", [])
        if len(listings) > 20:
            return {**tool_response, "listings": listings[:20], "_trimmed": len(listings) - 20}

    return None


# ── Agent builders ─────────────────────────────────────────────────────────────


def _build_merchandising_agent(store_name: str) -> Any:
    """Specialized sub-agent that owns the content enrichment pipeline."""
    try:
        from google.adk.agents import Agent  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("google-adk is required. pip install google-adk") from exc

    from .tools import (
        apply_change,
        classify_product_ontology,
        discard_change,
        enrich_product,
        generate_geo_content,
        generate_seo_content,
        get_listing,
        lookup_product_entities,
        search_listings,
        stage_listing_update,
    )

    merch_skill = _load_skills()  # injects the merchandising SKILL.md into instruction
    instruction = _MERCH_PROMPT.format(store_name=store_name)
    if merch_skill:
        instruction = instruction + "\n\n" + merch_skill

    return Agent(
        name="merchandising_agent",
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        description=(
            "Specialist in product enrichment, SEO copy, geographic variants, and ontology "
            "classification. Handles the full classify → enrich → stage workflow."
        ),
        instruction=instruction,
        before_model_callback=_before_model_cb,
        before_tool_callback=_before_tool_cb,
        after_tool_callback=_after_tool_cb,
        tools=[
            get_listing,
            search_listings,
            classify_product_ontology,
            enrich_product,
            generate_seo_content,
            generate_geo_content,
            lookup_product_entities,
            stage_listing_update,
            apply_change,
            discard_change,
        ],
    )


def _build_agent(store_name: str) -> Any:
    """Construct the orchestrator ADK Agent with callbacks and a merchandising sub-agent."""
    try:
        from google.adk.agents import Agent  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("google-adk is required. pip install google-adk") from exc

    from .tools import (
        apply_change,
        approve_change,
        discard_change,
        get_business_snapshot,
        get_inventory_alerts,
        get_listing,
        get_order_issues,
        get_pending_changes,
        get_pending_quote_approvals,
        run_analysis,
        search_listings,
        stage_inventory_action,
        stage_listing_update,
    )

    merch_agent = _build_merchandising_agent(store_name)
    skills = _load_skills()
    instruction = _ORCHESTRATOR_PROMPT.format(store_name=store_name)
    if skills:
        instruction = instruction + "\n\n" + skills

    return Agent(
        name="gemini_merchant_agent",
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        description=f"Merchant AI orchestrator for {store_name}",
        instruction=instruction,
        before_model_callback=_before_model_cb,
        before_tool_callback=_before_tool_cb,
        after_tool_callback=_after_tool_cb,
        sub_agents=[merch_agent],
        tools=[
            get_business_snapshot,
            get_inventory_alerts,
            get_order_issues,
            run_analysis,
            search_listings,
            get_listing,
            get_pending_changes,
            get_pending_quote_approvals,
            stage_listing_update,
            stage_inventory_action,
            approve_change,
            apply_change,
            discard_change,
        ],
    )


# ── SSE helper ─────────────────────────────────────────────────────────────────


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# ── Agent class ────────────────────────────────────────────────────────────────


def _build_session_service() -> Any:
    """Return VertexAiSessionService when GCP project is configured, else InMemorySessionService.

    Set GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION env vars to enable
    persistent, scalable session storage via Vertex AI. Without them the agent
    falls back to in-process memory (sessions are lost on restart).
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if project:
        try:
            from google.adk.sessions import VertexAiSessionService  # type: ignore[import]
            log.info("Using VertexAiSessionService (project=%s, location=%s)", project, location)
            return VertexAiSessionService(project=project, location=location)
        except Exception as exc:
            log.warning("VertexAiSessionService unavailable, falling back to InMemory: %s", exc)
    from google.adk.sessions import InMemorySessionService  # type: ignore[import]
    return InMemorySessionService()


class GeminiMerchantAgent:
    """One deployment of the Gemini ADK merchant agent — Path 2 (ADK Agent + Runner).

    ``runner`` and ``session_service`` are created once at startup and shared
    across all requests.

    ADK framework features active:
    - before_model_callback: injects date + store into every model call
    - before_tool_callback: provenance gate (write tools) + approval gate (apply_change)
    - after_tool_callback: provenance tracking, approval tracking, cache, trim
    - sub_agents: MerchandisingAgent handles all content generation tasks
    - session.state: store_name seeded at create_session; IDs tracked per turn
    - prime_session: proactively fetches briefing at login (no LLM round-trip)
    - Session service: VertexAiSessionService when GOOGLE_CLOUD_PROJECT is set,
      otherwise InMemorySessionService (sessions lost on restart)
    """

    def __init__(self, store_name: str) -> None:
        try:
            from google.adk.runners import Runner  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("google-adk is required. pip install google-adk") from exc

        self._store_name = store_name
        self._session_service = _build_session_service()
        self._runner = Runner(
            agent=_build_agent(store_name),
            app_name="gemini_merchant",
            session_service=self._session_service,
        )
        # Briefing cache populated by prime_session(); keyed by session_id
        self._briefings: dict[str, dict] = {}

    async def ensure_session(self, session_id: str) -> None:
        """Create the ADK session with seeded state if it does not exist yet."""
        initial_state = {"store_name": self._store_name}
        try:
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
                    state=initial_state,
                )
        except Exception:
            import contextlib
            with contextlib.suppress(Exception):
                await self._session_service.create_session(
                    app_name="gemini_merchant",
                    user_id="operator",
                    session_id=session_id,
                    state=initial_state,
                )

    async def prime_session(self, session_id: str) -> dict:
        """Proactively fetch a business briefing and cache it for the briefing endpoint.

        Called in the background after session creation so the operator can ask
        "what needs my attention?" and get an instant answer without waiting for
        the first tool call round-trip.
        """
        from .tools import _backend, _session as _make_session

        if _backend is None:
            return {}
        try:
            sess = _make_session()
            snap = await _backend.get_business_snapshot(sess)
            alerts = await _backend.get_inventory_alerts(sess)
            briefing: dict = {
                "snapshot": snap.model_dump(mode="json", exclude_none=True),
                "alerts_count": len(alerts),
                "top_alerts": [a.model_dump(mode="json", exclude_none=True) for a in alerts[:3]],
                "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
            }
            self._briefings[session_id] = briefing
            return briefing
        except Exception as exc:
            log.warning("prime_session failed: %s", exc)
            return {}

    def get_briefing(self, session_id: str) -> dict:
        """Return the most recent pre-fetched briefing for a session, or {}."""
        return self._briefings.get(session_id, {})

    async def stream_turn(
        self, session_id: str, user_message: str
    ) -> AsyncIterator[str]:
        """Run one agent turn and yield SSE frames.

        Yields text_delta frames while the model streams text, tool_call /
        tool_result frames around each function execution (including those from
        the merchandising sub-agent), and a final turn_complete frame.
        """
        from google.genai import types as gtypes  # type: ignore[import]

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
                # Accumulate token usage when available
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
