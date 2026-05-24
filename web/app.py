"""FastAPI playground for matscout — single-page chat over SSE.

POST a JSON ``{"query": "..."}`` to ``/api/query``. The server runs the
agent loop and streams events as they happen (``tool_call``,
``tool_result``, ``tool_error``, ``final``). The browser renders them
live, so you see the agent "think" instead of staring at a spinner.

Run:
    uv run uvicorn web.app:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from matscout.agent.runner import TraceEvent, stream_agent

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="matscout",
    description="Materials Project research agent — MCP server + OpenAI agent.",
    version="0.1.0",
)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


def _event_payload(ev: TraceEvent) -> dict[str, Any]:
    """Strip the trace event down to what the browser actually needs."""
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
        # Keep the payload compact — full result already drives the
        # final answer; here we just show a short preview/length.
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


async def _stream(query: str) -> AsyncIterator[dict[str, Any]]:
    """Bridge sync agent generator → async SSE generator."""
    loop = asyncio.get_running_loop()
    gen = stream_agent(query)

    while True:
        try:
            ev = await loop.run_in_executor(None, next, gen, None)
        except StopIteration:
            break
        if ev is None:
            break
        yield {"event": ev.kind, "data": json.dumps(_event_payload(ev), ensure_ascii=False)}
        if ev.kind == "final":
            break


@app.post("/api/query")
async def query(req: QueryRequest) -> EventSourceResponse:
    return EventSourceResponse(
        _stream(req.query),
        # SSE-starlette emits a `: ping` comment-line every 15s — keeps the
        # TCP socket warm against DPI / proxy idle-timeouts (we've seen RU
        # provider DPI drop streams that go silent > ~60s while gpt-4o is
        # composing the final answer).
        ping=15,
        headers={
            # Tell nginx and any upstream proxies not to buffer this response.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
        },
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
