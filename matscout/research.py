"""Persistent storage for research sessions.

Each agent run gets a snapshot: query, locale, full event trace, final
answer, timestamps. Snapshots live in SQLite alongside the tool-result
cache and are reachable by request_id forever (until the operator GCs).

The point: when you cite a matscout result in a thesis/paper, the reader
should be able to open the URL and see *exactly* the reasoning trail you
relied on — not a fresh agent run that might give a different answer.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research (
    request_id  TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    locale      TEXT NOT NULL,
    events_json TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    final_answer TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_started ON research(started_at);
"""


class ResearchStore:
    """Tiny append-only journal of every agent run we've completed."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(
        self,
        *,
        request_id: str,
        query: str,
        locale: str,
        events: list[dict[str, Any]],
        status: str,
        started_at: float,
        finished_at: float | None,
    ) -> None:
        """Upsert a run. Safe to call repeatedly as events accumulate."""
        final_answer: str | None = None
        for ev in events:
            if ev.get("kind") == "final":
                final_answer = ev.get("content")
                break

        with self._conn() as c:
            c.execute(
                """
                INSERT INTO research (
                    request_id, query, locale, events_json,
                    status, started_at, finished_at, final_answer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    events_json  = excluded.events_json,
                    status       = excluded.status,
                    finished_at  = excluded.finished_at,
                    final_answer = excluded.final_answer
                """,
                (
                    request_id,
                    query,
                    locale,
                    json.dumps(events, ensure_ascii=False, default=str),
                    status,
                    int(started_at),
                    int(finished_at) if finished_at is not None else None,
                    final_answer,
                ),
            )

    def load(self, request_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT request_id, query, locale, events_json, status,
                       started_at, finished_at, final_answer
                FROM research WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "request_id": row[0],
            "query": row[1],
            "locale": row[2],
            "events": json.loads(row[3]),
            "status": row[4],
            "started_at": row[5],
            "finished_at": row[6],
            "final_answer": row[7],
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent finished runs — useful for an index page later."""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT request_id, query, locale, status, started_at, finished_at
                FROM research
                WHERE status != 'running'
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "request_id": r[0],
                "query": r[1],
                "locale": r[2],
                "status": r[3],
                "started_at": r[4],
                "finished_at": r[5],
            }
            for r in rows
        ]

    def count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM research").fetchone()[0])


_store: ResearchStore | None = None


def get_store() -> ResearchStore:
    """Lazy singleton — created on first access."""
    global _store
    if _store is None:
        from matscout.config import get_settings

        settings = get_settings()
        path = settings.cache_dir / "research.db"
        _store = ResearchStore(path)
    return _store


def set_store(store: ResearchStore | None) -> None:
    """Test hook — inject a tmp-dir store or reset."""
    global _store
    _store = store
