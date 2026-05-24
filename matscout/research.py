"""Persistent storage for research sessions + citation helpers.

Each agent run gets a snapshot: query, locale, full event trace, final
answer, timestamps. Snapshots live in SQLite alongside the tool-result
cache and are reachable by request_id forever (until the operator GCs).

The point: when you cite a matscout result in a thesis/paper, the reader
should be able to open the URL and see *exactly* the reasoning trail you
relied on — not a fresh agent run that might give a different answer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Matches mp-149, mp-123456, mvc-12345 (Materials Project + Vasp Calc db).
MP_ID_RE = re.compile(r"\b(mp|mvc)-(\d+)\b")
MP_WEB = "https://next-gen.materialsproject.org/materials/{}"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research (
    request_id  TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    locale      TEXT NOT NULL,
    events_json TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    final_answer TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_started ON research(started_at);
"""

# Pragmatic migration — older deployments don't have metadata_json yet.
_MIGRATIONS = ("ALTER TABLE research ADD COLUMN metadata_json TEXT",)


class ResearchStore:
    """Tiny append-only journal of every agent run we've completed."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            for stmt in _MIGRATIONS:
                # ALTERs are idempotent at the application level — already-
                # applied migrations raise OperationalError, which is fine.
                with suppress(sqlite3.OperationalError):
                    c.execute(stmt)

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
        metadata: dict[str, Any] | None = None,
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
                    status, started_at, finished_at, final_answer, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    events_json   = excluded.events_json,
                    status        = excluded.status,
                    finished_at   = excluded.finished_at,
                    final_answer  = excluded.final_answer,
                    metadata_json = excluded.metadata_json
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
                    json.dumps(metadata, default=str) if metadata is not None else None,
                ),
            )

    def load(self, request_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT request_id, query, locale, events_json, status,
                       started_at, finished_at, final_answer, metadata_json
                FROM research WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        meta_raw = row[8] if len(row) > 8 else None
        return {
            "request_id": row[0],
            "query": row[1],
            "locale": row[2],
            "events": json.loads(row[3]),
            "status": row[4],
            "started_at": row[5],
            "finished_at": row[6],
            "final_answer": row[7],
            "metadata": json.loads(meta_raw) if meta_raw else {},
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


# ── Citation helpers ─────────────────────────────────────────────────────────


def extract_material_ids(snapshot: dict[str, Any]) -> list[str]:
    """Pull every distinct mp-XXX / mvc-XXX referenced in a run.

    Looks at tool_call args, tool_result summaries, and the final answer.
    Preserves order of first appearance — handy when the reader scans the
    citation list top-to-bottom.
    """
    seen: dict[str, None] = {}

    def harvest(text: str) -> None:
        for m in MP_ID_RE.finditer(text):
            seen.setdefault(f"{m.group(1)}-{m.group(2)}", None)

    for ev in snapshot.get("events", []):
        if isinstance(ev.get("args"), dict):
            for v in ev["args"].values():
                if isinstance(v, str):
                    harvest(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            harvest(item)
        if isinstance(ev.get("result_summary"), str):
            harvest(ev["result_summary"])
        if isinstance(ev.get("content"), str):
            harvest(ev["content"])

    if snapshot.get("final_answer"):
        harvest(snapshot["final_answer"])

    return list(seen.keys())


def reconstruct_messages_from_snapshot(
    snapshot: dict[str, Any], system_prompt: str
) -> list[dict[str, Any]]:
    """Rebuild a plain-text conversation history from a saved snapshot.

    Used when a visitor lands on a /r/{id} snapshot URL and wants to ask a
    follow-up. The Responses API path runs MCP server-side and doesn't
    accept chat.completions-shaped tool exchanges in its ``input`` list,
    so we serialize the original tool sequence into one synthetic
    assistant text block — preserves recall of what was called and what
    came back, in a form the agent can actually read.

    Output shape:
        [
            {role: 'system',    content: SYSTEM_PROMPT},
            {role: 'user',      content: original_query},
            {role: 'assistant', content: "Earlier in this session I…"},
        ]

    The synthetic assistant block reads like a memo from past-self —
    listing each tool name + (compact) args + summary line, plus the
    original final answer. If the follow-up question needs precise
    values, the agent can simply re-call the tool: the SQLite cache will
    hand back the same result for free.
    """
    query = snapshot.get("query", "")
    events: list[dict[str, Any]] = snapshot.get("events") or []

    # Walk the trace and build a single assistant memo describing what
    # past-self did. Tool calls are paired with their results by
    # position (output_index alignment is lost in the snapshot, but the
    # trace order matches), errors get the same treatment as results.
    memo_parts: list[str] = [
        "Earlier in this same research session you (the agent) executed the "
        "tool calls below and produced the final answer at the end. The user "
        "is now asking a follow-up — assume the user has seen everything below."
    ]

    pending_calls: list[tuple[str, dict[str, Any]]] = []
    pending_results: list[tuple[str, str]] = []  # (summary, error)
    final_answer = ""

    def flush_turn() -> None:
        nonlocal pending_calls, pending_results
        for (name, args), (summary, error) in zip(pending_calls, pending_results, strict=False):
            args_str = (
                ", ".join(f"{k}={v!r}" for k, v in (args or {}).items() if v is not None)
                or "(no args)"
            )
            if error:
                memo_parts.append(f"  - {name}({args_str}) → ERROR: {error[:120]}")
            else:
                memo_parts.append(f"  - {name}({args_str}) → {summary[:200]}")
        pending_calls = []
        pending_results = []

    for ev in events:
        kind = ev.get("kind")
        if kind == "tool_call":
            pending_calls.append((ev.get("name", ""), ev.get("args") or {}))
        elif kind == "tool_result":
            pending_results.append((ev.get("result_summary") or "(ok)", ""))
        elif kind == "tool_error":
            pending_results.append(("", ev.get("error", "tool failed")))
        elif kind == "thinking":
            flush_turn()
        elif kind == "final":
            flush_turn()
            final_answer = ev.get("content") or ""

    flush_turn()
    if final_answer:
        memo_parts.append("")
        memo_parts.append("Your final answer to the user was:")
        memo_parts.append(final_answer)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
        {"role": "assistant", "content": "\n".join(memo_parts)},
    ]


def _ts_to_iso(ts: int | float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_bibtex(snapshot: dict[str, Any], base_url: str = "https://matscout.prfo.design") -> str:
    """Build a BibTeX export covering the run itself + every referenced mp-id.

    Each MP entry gets the canonical Jain-2013 attribution (the Materials
    Project's own preferred citation). matscout itself gets a single @misc
    pointing at the persistent /r/{id} URL.
    """
    rid = snapshot["request_id"]
    ts = _ts_to_iso(snapshot.get("started_at"))
    year = ts[:4] or "2026"
    query_clean = (snapshot.get("query") or "").replace("\n", " ").strip()[:200]
    share_url = f"{base_url}/r/{rid}"

    out: list[str] = []
    out.append(
        f"@misc{{matscout-{rid},\n"
        f"  author       = {{Lev (matscout)}},\n"
        f"  title        = {{matscout research session: {query_clean}}},\n"
        f"  year         = {{{year}}},\n"
        f"  howpublished = {{\\url{{{share_url}}}}},\n"
        f"  note         = {{Persistent agent run over the Materials Project, accessed {ts}}}\n"
        f"}}\n"
    )

    for mid in extract_material_ids(snapshot):
        out.append(
            f"@misc{{{mid},\n"
            f"  author       = {{Jain, Anubhav and Ong, Shyue Ping and Hautier, Geoffroy and Chen, Wei and Richards, William Davidson and Dacek, Stephen and Cholia, Shreyas and Gunter, Dan and Skinner, David and Ceder, Gerbrand and Persson, Kristin A.}},\n"
            f"  title        = {{{{Materials Project entry {mid}}}}},\n"
            f"  year         = {{2013}},\n"
            f"  howpublished = {{\\url{{{MP_WEB.format(mid)}}}}},\n"
            f"  note         = {{The Materials Project: A materials genome approach to accelerating materials innovation. APL Materials 1(1), 011002 (2013). DOI: 10.1063/1.4812323}}\n"
            f"}}\n"
        )

    return "\n".join(out)
