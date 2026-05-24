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
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from matscout.agent.runner import TraceEvent, stream_agent
from matscout.provenance import build_metadata
from matscout.research import (
    extract_material_ids,
    get_store,
    reconstruct_messages_from_snapshot,
    to_bibtex,
)
from matscout.tool_facades import get_structure as _get_structure_facade


def _phase_diagram_viz(result: dict[str, Any]) -> dict[str, Any]:
    """Trim a get_phase_diagram / get_competing_phases result to chart fodder.

    For binary chemsys we also compute the mole fraction of the *second* element
    (so the X axis can be a proper composition slider 0..1 in the browser).
    For unary/ternary/higher we just hand back scatter coordinates; the
    browser decides which projection to draw.
    """
    chemsys_str: str = result.get("chemsys") or result.get("anchor_formula") or ""
    # Sorted, canonical element order — same as MP's chemsys formatting.
    elements: list[str] = sorted({el for el in chemsys_str.replace(",", "-").split("-") if el})

    points: list[dict[str, Any]] = []
    for is_stable, src_key in ((True, "stable_phases"), (False, "metastable_phases")):
        for p in result.get(src_key, []) or []:
            fe = p.get("formation_energy_per_atom")
            eah = p.get("energy_above_hull")
            if fe is None and eah is None:
                continue
            pt: dict[str, Any] = {
                "material_id": p.get("material_id", ""),
                "formula": p.get("formula_pretty", ""),
                "formation_energy_per_atom": fe,
                "energy_above_hull": eah,
                "is_stable": bool(is_stable),
            }
            # Composition: number of atoms of each element / total atoms.
            # For binary, the browser will use this for the convex-hull plot.
            comp = _parse_composition(p.get("formula", "") or p.get("formula_pretty", ""))
            if comp and elements:
                total = sum(comp.values()) or 1
                pt["composition"] = {el: comp.get(el, 0) / total for el in elements}
            points.append(pt)

    # For the visualisation, collapse polymorphs that share a composition to
    # a single lowest-energy representative. A binary hull plot draws one
    # point per composition (the lowest-energy polymorph anchors the hull;
    # higher polymorphs would just stack on the same x). The full polymorph
    # list still goes to the LLM via the un-trimmed tool result.
    if len(elements) == 2:
        by_comp: dict[tuple[float, ...], dict[str, Any]] = {}
        for p in points:
            p_comp = p.get("composition")
            fe = p.get("formation_energy_per_atom")
            if not isinstance(p_comp, dict) or not isinstance(fe, int | float):
                continue
            key = tuple(round(p_comp.get(el, 0.0), 6) for el in elements)
            cur = by_comp.get(key)
            if cur is None or fe < cur["formation_energy_per_atom"]:
                by_comp[key] = p
        viz_points = sorted(
            by_comp.values(),
            key=lambda p: p["composition"].get(elements[1], 0.0),
        )
    else:
        viz_points = points

    return {
        "chemsys": chemsys_str,
        "elements": elements,
        "n_stable": result.get("n_stable") or 0,
        "n_metastable": result.get("n_metastable") or 0,
        "points": viz_points,
    }


def _parse_composition(formula: str) -> dict[str, int]:
    """Tiny pure-Python formula parser: 'Li2FeO4' -> {Li:2, Fe:1, O:4}.

    Good enough for the pretty formulas MP returns (single-letter or
    two-letter element symbols, optional integer subscripts, no
    parentheses). Returns {} when we can't make sense of it.
    """
    import re

    if not formula:
        return {}
    out: dict[str, int] = {}
    for el, n in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
        if not el:
            continue
        out[el] = out.get(el, 0) + (int(n) if n else 1)
    return out


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
    # Hold a strong reference to the background task so the event loop
    # doesn't GC it mid-flight (RUF006).
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    conversation_id: str | None = None


@dataclass
class Conversation:
    """In-memory store of a multi-turn agent conversation.

    Each turn's full OpenAI message list (system + accumulated user, tool,
    and assistant messages) is parked here. A follow-up POST /api/query
    referencing this ``conversation_id`` resumes from these messages so the
    agent retains context across questions.
    """

    conversation_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    locale: str = "en"
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)


_runs: dict[str, Run] = {}
_conversations: dict[str, Conversation] = {}
_runs_lock = asyncio.Lock()
CONVERSATION_TTL_SECONDS = 3600  # 1 h — covers a long research session


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
        # For phase-diagram tools, embed a compact `viz` payload so the
        # browser can render an inline SVG chart without re-fetching.
        if ev.name in ("get_phase_diagram", "get_competing_phases") and isinstance(ev.result, dict):
            payload["viz"] = _phase_diagram_viz(ev.result)
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


def _persist(run: Run, *, include_metadata: bool = False) -> None:
    """Append the current run state to the on-disk research journal.

    Provenance metadata is attached at the end of a run (it's stable across
    a single execution; no point computing it on every event).
    """
    # Never let storage failures crash an in-flight agent loop.
    # The in-memory run object is still the source of truth for polling.
    with contextlib.suppress(Exception):
        get_store().save(
            request_id=run.request_id,
            query=run.query,
            locale=run.locale,
            events=run.events,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            metadata=build_metadata() if include_metadata else None,
        )


async def _drive_run(run: Run) -> None:
    """Background coroutine — runs the agent and pumps events into ``run.events``."""
    loop = asyncio.get_running_loop()
    # Steer the LLM's natural-language output without touching the tool layer.
    # The system prompt stays English (tool docs / matsci jargon don't benefit
    # from translation); we just bolt on a "respond in <lang>" directive.
    locale_hint = ""
    if run.locale == "ru":
        locale_hint = (
            "\n\nВажно: финальный ответ пользователю — на русском языке. "  # noqa: RUF001
            "Технические термины и имена материалов оставляй как есть "
            "(band gap, mp-149, Fd-3m, eV/atom). "
            "Внутренние tool calls и рассуждения — на английском."
        )
    from matscout.agent.prompts import SYSTEM_PROMPT_V1

    # If this run continues an existing conversation, hand the prior message
    # list to the runner so context carries over across turns.
    prior_messages: list[dict[str, Any]] | None = None
    if run.conversation_id is not None:
        conv = _conversations.get(run.conversation_id)
        if conv is not None and conv.messages:
            prior_messages = conv.messages

    gen = stream_agent(
        run.query,
        system_prompt=SYSTEM_PROMPT_V1 + locale_hint,
        prior_messages=prior_messages,
    )
    final_state: list[dict[str, Any]] | None = None
    try:
        while True:
            ev = await loop.run_in_executor(None, next, gen, None)
            if ev is None:
                break
            run.events.append(_event_payload(ev))
            # Persist after every event so an interrupted server still leaves
            # a recoverable partial trace on disk.
            _persist(run)
            if ev.kind == "final":
                final_state = ev.state_messages
                break
    except Exception as e:
        run.events.append({"kind": "tool_error", "error": f"{type(e).__name__}: {e}"})
        run.status = "error"
        run.error = str(e)
    else:
        run.status = "done"
        # Bank the post-turn message list against the conversation so the
        # next /api/query with this conversation_id resumes cleanly.
        if run.conversation_id is not None and final_state is not None:
            conv = _conversations.get(run.conversation_id)
            if conv is not None:
                conv.messages = final_state
                conv.last_used_at = time.time()
    finally:
        run.finished_at = time.time()
        _persist(run, include_metadata=True)


async def _gc_old_runs() -> None:
    """Drop runs that finished more than RUN_TTL_SECONDS ago.

    Also evict conversations whose last use is past CONVERSATION_TTL_SECONDS;
    these hold the full message list which is the priciest piece of state
    on the server.
    """
    now = time.time()
    async with _runs_lock:
        stale = [
            rid
            for rid, r in _runs.items()
            if r.finished_at is not None and now - r.finished_at > RUN_TTL_SECONDS
        ]
        for rid in stale:
            _runs.pop(rid, None)
        stale_convs = [
            cid
            for cid, c in _conversations.items()
            if now - c.last_used_at > CONVERSATION_TTL_SECONDS
        ]
        for cid in stale_convs:
            _conversations.pop(cid, None)


# ── HTTP surface ─────────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    locale: Literal["en", "ru"] = "en"
    # When set, this turn continues an existing conversation (the runner
    # picks up the prior message list). When omitted, the server starts a
    # fresh conversation and returns its id so the client can chain.
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class QueryAccepted(BaseModel):
    request_id: str
    conversation_id: str


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
        # Look up or open a conversation. Clients hand back the id we issued;
        # if it's unknown to us (e.g. server restart), we silently mint a new
        # one rather than 400 — the cost is just losing prior context, not
        # rejecting the user's question.
        cid = req.conversation_id
        if cid is None or cid not in _conversations:
            cid = uuid.uuid4().hex[:12]
            _conversations[cid] = Conversation(conversation_id=cid, locale=req.locale)
        else:
            _conversations[cid].last_used_at = time.time()
        rid = uuid.uuid4().hex[:12]
        run = Run(request_id=rid, query=req.query, locale=req.locale, conversation_id=cid)
        _runs[rid] = run
    run.task = asyncio.create_task(_drive_run(run))
    return QueryAccepted(request_id=rid, conversation_id=cid)


@app.get("/api/query/{request_id}", response_model=PollResponse)
async def poll_query(request_id: str, since: int = 0) -> PollResponse:
    """Return all events recorded since index ``since`` for this run.

    Tries the in-memory registry first (current TTL window); falls back to
    the persistent research store so an old session keeps responding to
    poll requests forever.
    """
    run = _runs.get(request_id)
    if run is not None:
        return PollResponse(
            request_id=request_id,
            status=run.status,
            total_events=len(run.events),
            events=run.events[since:],
        )
    persisted = get_store().load(request_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="unknown request_id")
    events = persisted["events"]
    return PollResponse(
        request_id=request_id,
        status=persisted["status"],
        total_events=len(events),
        events=events[since:],
    )


# ── /r/{id} — permanent snapshot view ────────────────────────────────────────


@app.get("/api/research/{request_id}")
async def get_research(request_id: str) -> dict[str, Any]:
    """JSON snapshot of a saved research session (everything we know about it)."""
    persisted = get_store().load(request_id)
    if persisted is None:
        raise HTTPException(status_code=404, detail="unknown request_id")
    return persisted


@app.get("/r/{request_id}", response_class=HTMLResponse)
async def view_research(request_id: str) -> HTMLResponse:
    """Serve the SPA — the JS sniffs the URL path and replays the saved run."""
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


class ResumeResponse(BaseModel):
    conversation_id: str
    n_messages: int


@app.post("/api/research/{request_id}/resume", response_model=ResumeResponse)
async def resume_from_snapshot(request_id: str) -> ResumeResponse:
    """Reconstruct an OpenAI conversation from a saved snapshot's trace.

    Lets a visitor land on /r/{id} and ask a follow-up question with the
    original run's full context. We rebuild the message list (system,
    original user query, the assistant↔tool exchange, the original final
    answer), park it in the in-memory _conversations registry, and hand
    back the new conversation_id so the client can pass it on subsequent
    POST /api/query calls.
    """
    snapshot = get_store().load(request_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="unknown request_id")
    from matscout.agent.prompts import SYSTEM_PROMPT_V1

    messages = reconstruct_messages_from_snapshot(snapshot, SYSTEM_PROMPT_V1)
    cid = uuid.uuid4().hex[:12]
    locale = snapshot.get("locale") or "en"
    _conversations[cid] = Conversation(
        conversation_id=cid,
        messages=messages,
        locale=locale,
    )
    return ResumeResponse(conversation_id=cid, n_messages=len(messages))


@app.get("/api/research/{request_id}/citation")
async def citation_bibtex(request_id: str, request: Request) -> Response:
    """BibTeX with one @misc per material referenced in the run."""
    snapshot = get_store().load(request_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="unknown request_id")
    base = f"{request.url.scheme}://{request.url.netloc}"
    bib = to_bibtex(snapshot, base_url=base)
    return Response(
        content=bib,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="matscout-{request_id}.bib"',
        },
    )


@app.get("/api/structure/{material_id}.{fmt}")
async def download_structure(material_id: str, fmt: str) -> Response:
    """Stream the structure file with attachment-Disposition so browsers save it.

    Bypasses the agent entirely — useful for the Download CIF button next
    to each mp-id link in the rendered answer.
    """
    if fmt not in {"cif", "poscar", "xyz"}:
        raise HTTPException(status_code=400, detail=f"unsupported format: {fmt}")
    try:
        result = _get_structure_facade(material_id, fmt=fmt)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return Response(
        content=result["content"],
        media_type="chemical/x-cif" if fmt == "cif" else "text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{result["filename_suggestion"]}"',
        },
    )


@app.get("/api/research/{request_id}/materials")
async def list_materials(request_id: str) -> dict[str, Any]:
    """Returns every mp-id mentioned in this run with its MP web URL."""
    snapshot = get_store().load(request_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="unknown request_id")
    from matscout.research import MP_WEB

    ids = extract_material_ids(snapshot)
    return {
        "request_id": request_id,
        "count": len(ids),
        "materials": [{"material_id": mid, "url": MP_WEB.format(mid)} for mid in ids],
    }


# Mount the SPA at the root. /api/* still wins because FastAPI matches
# explicit routes before the StaticFiles catch-all.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
