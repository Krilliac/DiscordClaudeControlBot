"""
Tiny SQLite-backed store for the active Claude Agent SDK session id.

The SDK persists actual conversation messages to its own files under
`~/.claude/projects/...`. All we need to remember across service restarts
is which session id is "ours" so we can resume the same conversation
instead of starting a fresh one.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    session_id TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_UPSERT_SQL = """
INSERT INTO session_state (id, session_id) VALUES (1, ?)
ON CONFLICT (id) DO UPDATE SET
    session_id = excluded.session_id,
    updated_at = CURRENT_TIMESTAMP
"""


class SessionIdStore:
    def __init__(self, db_path: Path | str) -> None:
        self._db = Path(db_path)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def load(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT session_id FROM session_state WHERE id = 1").fetchone()
        if row is None:
            return None
        value = row[0]
        if not isinstance(value, str) or not value:
            return None
        return value

    def save(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        with self._connect() as conn:
            conn.execute(_UPSERT_SQL, (session_id,))
        log.debug("persisted session_id=%s to %s", session_id, self._db)

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE session_state SET session_id = NULL WHERE id = 1")
