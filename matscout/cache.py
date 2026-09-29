"""SQLite-backed cache for tool results.

Why SQLite (not just files on disk):
- Atomic writes, no temp-file dance.
- Indexed lookups stay sub-millisecond even with thousands of entries.
- One file, easy to ship / inspect with `sqlite3 cache/matscout.db`.

Key invariants:
- Cache key is sha256(tool_name + canonical_json(args)). Reordering keys in
  args does not invalidate the cache.
- TTL is enforced on read: stale rows are filtered out (and lazily pruned by
  ``vacuum()``). We never auto-update timestamps on hits; the entry's age
  is bounded by ``ttl_seconds`` from the moment it was put.
- The cache is read-mostly. Writes are protected by SQLite's own locking.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_cache (
    cache_key   TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    hit_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tool_created ON tool_cache(tool_name, created_at);
"""


def _canonical_args(args: dict[str, Any]) -> str:
    """Stable JSON serialization - same args in any key order → same string."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


def _hash(tool_name: str, args: dict[str, Any]) -> str:
    raw = f"{tool_name}|{_canonical_args(args)}".encode()
    return hashlib.sha256(raw).hexdigest()


class Cache:
    """A tiny TTL-bounded KV cache over SQLite."""

    def __init__(
        self,
        db_path: Path,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self._clock = clock
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

    # ---- public API ----
    def get(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Returns cached result or None if missing / expired."""
        key = _hash(tool_name, args)
        now = self._clock()
        with self._conn() as c:
            row = c.execute(
                "SELECT result_json, created_at FROM tool_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            result_json, created_at = row
            if now - created_at > self.ttl_seconds:
                # Stale - let it be pruned later by vacuum(), don't return it.
                return None
            c.execute(
                "UPDATE tool_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (key,),
            )
            data: dict[str, Any] = json.loads(result_json)
            return data

    def put(self, tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        """Upserts a fresh entry. Overwrites any existing key (resets TTL)."""
        key = _hash(tool_name, args)
        now = self._clock()
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO tool_cache (cache_key, tool_name, args_json, result_json,
                                        created_at, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    result_json = excluded.result_json,
                    created_at  = excluded.created_at,
                    hit_count   = 0
                """,
                (key, tool_name, _canonical_args(args), json.dumps(result, default=str), now),
            )

    def clear(self, tool_name: str | None = None) -> int:
        """Delete entries; returns the row count removed."""
        with self._conn() as c:
            if tool_name is None:
                cur = c.execute("DELETE FROM tool_cache")
            else:
                cur = c.execute("DELETE FROM tool_cache WHERE tool_name = ?", (tool_name,))
            return cur.rowcount

    def vacuum(self) -> int:
        """Prune expired rows. Returns the row count removed."""
        cutoff = self._clock() - self.ttl_seconds
        with self._conn() as c:
            cur = c.execute("DELETE FROM tool_cache WHERE created_at < ?", (cutoff,))
            return cur.rowcount

    def stats(self) -> dict[str, Any]:
        """High-level counters for observability / debugging."""
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM tool_cache").fetchone()[0]
            hits = c.execute("SELECT COALESCE(SUM(hit_count), 0) FROM tool_cache").fetchone()[0]
            by_tool_rows = c.execute(
                "SELECT tool_name, COUNT(*), COALESCE(SUM(hit_count), 0) "
                "FROM tool_cache GROUP BY tool_name ORDER BY tool_name"
            ).fetchall()
        by_tool = {name: {"entries": cnt, "hits": h} for name, cnt, h in by_tool_rows}
        return {"total_entries": total, "total_hits": hits, "by_tool": by_tool}
