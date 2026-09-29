"""Path 3 — Vertex AI Agent Engine.

Wraps the ADK merchant agent in vertexai's AdkApp for managed hosting.
Equivalent to managed-agents/ in the Claude merchant agent path.

Usage:
    from app import create_merchant_app
    app = create_merchant_app("DreamHaus")
    # Then deploy via deploy.py or use locally for testing:
    response = app.query(input="What's our inventory status?", session_id="test-1")
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# Make examples/ importable so ``from gemini.api.agent import _build_agent`` resolves.
_examples_dir = str(Path(__file__).parent.parent.parent)
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

log = logging.getLogger(__name__)

APP_NAME = "gemini_merchant"
USER_ID = "operator"


# ── Local fallback runner ─────────────────────────────────────────────────────


class _LocalApp:
    """Minimal AdkApp-compatible wrapper for local testing without GCP.

    Exposes the same ``.query()`` interface as ``reasoning_engines.AdkApp``
    so callers don't need to branch on whether vertexai is installed.
    """

    def __init__(self, agent: Any) -> None:
        try:
            from google.adk.runners import Runner  # type: ignore[import]
            from google.adk.sessions import InMemorySessionService  # type: ignore[import]
        except ImportError as exc:
            raise ImportError("google-adk is required: pip install google-adk") from exc

        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=self._session_service,
        )

    def query(self, *, input: str, session_id: str) -> dict[str, Any]:
        """Run one synchronous turn and return the final text response."""
        return asyncio.get_event_loop().run_until_complete(
            self._query_async(input=input, session_id=session_id)
        )

    async def _query_async(self, *, input: str, session_id: str) -> dict[str, Any]:
        from google.genai import types as gtypes  # type: ignore[import]

        # Ensure session exists
        existing = await self._session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        if existing is None:
            await self._session_service.create_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id, state={}
            )

        text_parts: list[str] = []
        async for event in self._runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=gtypes.Content(
                role="user", parts=[gtypes.Part(text=input)]
            ),
        ):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

        return {"output": "".join(text_parts), "session_id": session_id}


# ── Public factory ────────────────────────────────────────────────────────────


def create_merchant_app(store_name: str) -> Any:
    """Return an AdkApp (or local fallback) wrapping the merchant agent.

    When ``vertexai`` is installed the agent is wrapped in
    ``reasoning_engines.AdkApp`` ready for deployment to Vertex AI Agent
    Engine.  Without ``vertexai`` a lightweight local wrapper is returned
    instead so the same code path can be exercised in unit tests or a dev
    shell without GCP credentials.

    Args:
        store_name: Display name of the store, e.g. ``"DreamHaus"``.

    Returns:
        An object with a ``.query(input, session_id)`` method.
    """
    from gemini.api.agent import _build_agent  # type: ignore[import]

    agent = _build_agent(store_name)

    try:
        from vertexai.preview import reasoning_engines  # type: ignore[import]

        log.info("vertexai available — wrapping in AdkApp")
        return reasoning_engines.AdkApp(agent=agent, enable_tracing=True)
    except ImportError:
        log.info("vertexai not installed — using local fallback runner")
        return _LocalApp(agent=agent)
