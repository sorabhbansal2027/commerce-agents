"""SQLite-backed session service for local/self-hosted deployments.

Provides the same interface as ADK's InMemorySessionService but persists
sessions across process restarts. Use VertexAiSessionService for production
GCP deployments instead.

Note: this implements only the subset of the full ADK SessionService interface
used by GeminiMerchantAgent (create_session, get_session, update_session).
Methods required by the broader ADK framework (list_sessions, delete_session,
append_event, etc.) are not implemented; importing this service into a standard
ADK Runner will raise NotImplementedError for those paths.

Usage:
    from session_service import SQLiteSessionService
    session_service = SQLiteSessionService(db_path=".sessions.db")
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Session object ────────────────────────────────────────────────────────────


@dataclass
class _Session:
    """Minimal session object returned by get_session."""

    app_name: str
    user_id: str
    session_id: str
    state: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


# ── Service ───────────────────────────────────────────────────────────────────


class SQLiteSessionService:
    """Async session service backed by a local SQLite database.

    Drop-in replacement for ADK's ``InMemorySessionService`` for local and
    self-hosted deployments that need sessions to survive process restarts.

    Args:
        db_path: Path to the SQLite database file.  Created automatically if
            it does not exist.
    """

    def __init__(self, db_path: str = ".merchant_sessions.db") -> None:
        self._db_path = db_path
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    app_name   TEXT        NOT NULL,
                    user_id    TEXT        NOT NULL,
                    session_id TEXT        PRIMARY KEY,
                    state      TEXT        NOT NULL DEFAULT '{}',
                    messages   TEXT        NOT NULL DEFAULT '[]',
                    created_at TEXT        NOT NULL
                )
                """
            )
            conn.commit()

    # ── Sync helpers (run inside asyncio.to_thread) ───────────────────────────

    def _create_sync(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        state: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions
                    (app_name, user_id, session_id, state, messages, created_at)
                VALUES (?, ?, ?, ?, '[]', ?)
                """,
                (app_name, user_id, session_id, json.dumps(state), now),
            )
            conn.commit()

    def _get_sync(
        self, app_name: str, user_id: str, session_id: str
    ) -> _Session | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT app_name, user_id, session_id, state, created_at
                FROM sessions
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                """,
                (app_name, user_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return _Session(
            app_name=row[0],
            user_id=row[1],
            session_id=row[2],
            state=json.loads(row[3]),
            created_at=row[4],
        )

    def _update_sync(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        state: dict[str, Any],
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE sessions SET state = ?
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                """,
                (json.dumps(state), app_name, user_id, session_id),
            )
            conn.commit()

    # ── Async public API ──────────────────────────────────────────────────────

    async def create_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        state: dict[str, Any] | None = None,
    ) -> _Session:
        """Insert a new session row; silently no-ops if it already exists.

        Args:
            app_name: ADK application name.
            user_id: Identifier for the user/operator.
            session_id: Unique session identifier.
            state: Optional initial state dict.

        Returns:
            The created (or existing) session object.
        """
        initial_state = state or {}
        await asyncio.to_thread(
            self._create_sync, app_name, user_id, session_id, initial_state
        )
        session = await self.get_session(app_name, user_id, session_id)
        assert session is not None  # just inserted
        return session

    async def get_session(
        self, app_name: str, user_id: str, session_id: str
    ) -> _Session | None:
        """Return the session or ``None`` if it does not exist.

        Args:
            app_name: ADK application name.
            user_id: Identifier for the user/operator.
            session_id: Unique session identifier.

        Returns:
            A ``_Session`` with a populated ``.state`` dict, or ``None``.
        """
        return await asyncio.to_thread(
            self._get_sync, app_name, user_id, session_id
        )

    async def update_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        state: dict[str, Any],
    ) -> None:
        """Overwrite the session's state JSON.

        Args:
            app_name: ADK application name.
            user_id: Identifier for the user/operator.
            session_id: Unique session identifier.
            state: New state dict to persist.
        """
        await asyncio.to_thread(
            self._update_sync, app_name, user_id, session_id, state
        )
