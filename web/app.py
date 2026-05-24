"""FastAPI playground for matscout — polling-based agent runner.

Originally this used SSE, but Russian-ISP DPI kept ripping long-lived
``text/event-stream`` connections regardless of heartbeat frequency.
Switched to a poll-friendly request-id pattern:

  POST /api/query              -> {"request_id": "..."}            (instant)
  GET  /api/query/{id}?since=N -> {"events": [...], "status": ...}  (short, repeatable)

Each poll is a short HTTPS round-trip — DPI sees the same pattern as a
normal REST API and leaves it alone. Browser polls every 500 ms.

Run:
    uv run uvicorn web.app:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from matscout.agent.runner import TraceEvent, stream_agent

STATIC_DIR = Path(__file__).parent / "static"
RUN_TTL_SECONDS = 600  # how long we keep a finished run around for late pollers
MAX_CONCURRENT_RUNS = 8

app = FastAPI(
    title="matscout",
    description="Materials Project research agent — MCP server + OpenAI agent.",
    version="0.1.0",
)


# ── In-memory run registry ──────────────────────────────────────────────────
# This is intentionally a single-process dict — one uvicorn worker, low
# concurrency. If we ever scale out we'd put this in Redis, but the cost of
# that abstraction now isn't worth it.

Status = Literal["running", "done", "error"]


@dataclass
class Run:
    request_id: str
    query: str
    locale: str = "en"
    events: list[dict[str, Any]] = field(default_factory=list)
    status: Status = "running"
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


_runs: dict[str, Run] = {}
_runs_lock = asyncio.Lock()


def _event_payload(ev: TraceEvent) -> dict[str, Any]:
    """Strip a TraceEvent down to what the browser actually needs."""
    payload: dict[str, Any] = {"kind": ev.kind}
    if ev.name is not None:
        payload["name"] = ev.name
    if ev.args is not None:
        payload["args"] = ev.args
    if ev.error is not None:
        payload["error"] = ev.error
    if ev.content is not None:
        payload["content"] = ev.content
    if ev.turn is not None:
        payload["turn"] = ev.turn
    if ev.result is not None:
        if isinstance(ev.result, list):
            preview: list[str] = []
            for item in ev.result[:3]:
                if isinstance(item, dict):
                    mid = item.get("material_id", "?")
                    formula = item.get("formula_pretty", "")
                    preview.append(f"{mid} {formula}".strip())
            payload["result_summary"] = f"{len(ev.result)} item(s)" + (
                f" → {', '.join(preview)}" + ("…" if len(ev.result) > 3 else "") if preview else ""
            )
        elif isinstance(ev.result, dict):
            mid = ev.result.get("material_id")
            formula = ev.result.get("formula_pretty")
            verdict = ev.result.get("verdict")
            parts: list[str] = []
            if mid:
                parts.append(mid)
            if formula:
                parts.append(formula)
            if verdict:
                parts.append(verdict)
            payload["result_summary"] = " · ".join(parts) if parts else "(object)"
        else:
            payload["result_summary"] = str(ev.result)[:120]
    return payload


async def _drive_run(run: Run) -> None:
    """Background coroutine — runs the agent and pumps events into ``run.events``."""
    loop = asyncio.get_running_loop()
    # Steer the LLM's natural-language output without touching the tool layer.
    # The system prompt stays English (tool docs / matsci jargon don't benefit
    # from translation); we just bolt on a "respond in <lang>" directive.
    locale_hint = ""  # noqa: RUF001 — Russian text intentionally embedded
    if run.locale == "ru":
        # ruff RUF001 flags Cyrillic chars as look-alikes of Latin; we want
        # actual Cyrillic here — silence the rule for this single block.
        locale_hint = (  # noqa: RUF001
            "\n\nВажно: финальный ответ пользователю — на русском языке. "  # noqa: RUF001
            "Технические термины и имена материалов оставляй как есть "  # noqa: RUF001
            "(band gap, mp-149, Fd-3m, eV/atom). "  # noqa: RUF001
            "Внутренние tool calls и рассуждения — на английском."  # noqa: RUF001
        )
    from matscout.agent.prompts import SYSTEM_PROMPT_V1

    gen = stream_agent(run.query, system_prompt=SYSTEM_PROMPT_V1 + locale_hint)
    try:
        while True:
            ev = await loop.run_in_executor(None, next, gen, None)
            if ev is None:
                break
            run.events.append(_event_payload(ev))
            if ev.kind == "final":
                break
    except Exception as e:
        run.events.append({"kind": "tool_error", "error": f"{type(e).__name__}: {e}"})
        run.status = "error"
        run.error = str(e)
    else:
        run.status = "done"
    finally:
        run.finished_at = time.time()


async def _gc_old_runs() -> None:
    """Drop runs that finished more than RUN_TTL_SECONDS ago."""
    now = time.time()
    async with _runs_lock:
        stale = [
            rid
            for rid, r in _runs.items()
            if r.finished_at is not None and now - r.finished_at > RUN_TTL_SECONDS
        ]
        for rid in stale:
            _runs.pop(rid, None)


# ── HTTP surface ─────────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    locale: Literal["en", "ru"] = "en"


class QueryAccepted(BaseModel):
    request_id: str


class PollResponse(BaseModel):
    request_id: str
    status: Status
    total_events: int
    events: list[dict[str, Any]]


@app.post("/api/query", response_model=QueryAccepted)
async def start_query(req: QueryRequest) -> QueryAccepted:
    """Kick off an agent run; returns immediately with a request_id to poll."""
    await _gc_old_runs()
    async with _runs_lock:
        in_flight = sum(1 for r in _runs.values() if r.status == "running")
        if in_flight >= MAX_CONCURRENT_RUNS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many concurrent runs ({in_flight}/{MAX_CONCURRENT_RUNS}). Retry shortly.",
            )
        rid = uuid.uuid4().hex[:12]
        run = Run(request_id=rid, query=req.query, locale=req.locale)
        _runs[rid] = run
    asyncio.create_task(_drive_run(run))
    return QueryAccepted(request_id=rid)


@app.get("/api/query/{request_id}", response_model=PollResponse)
async def poll_query(request_id: str, since: int = 0) -> PollResponse:
    """Return all events recorded since index ``since`` for this run."""
    run = _runs.get(request_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown request_id (may have expired)")
    return PollResponse(
        request_id=request_id,
        status=run.status,
        total_events=len(run.events),
        events=run.events[since:],
    )


# Mount the SPA at the root. /api/* still wins because FastAPI matches
# explicit routes before the StaticFiles catch-all.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
