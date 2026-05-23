"""OpenAI function-calling loop for matscout.

The agent receives a natural-language query, plans through repeated
gpt-4o tool calls (dispatched to ``tool_facades``), and finally returns
a synthesized markdown answer plus a structured trace of what it did.

Self-correction is *prompt-driven*: the system prompt tells gpt-4o how to
react to zero-hit and overflowing-hit conditions. We don't hard-wire
relax/tighten logic in Python — that would let us claim "agentic" without
actually being so.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from matscout.agent.openai_adapter import tools_to_openai_specs
from matscout.agent.prompts import SYSTEM_PROMPT_V1
from matscout.config import get_settings
from matscout.tool_facades import ALL_TOOLS

log = logging.getLogger("matscout.agent")

# Map tool name → callable. Used both to build the OpenAI spec and to
# dispatch tool_call deltas back to Python.
TOOL_TABLE: dict[str, Any] = {fn.__name__: fn for fn in ALL_TOOLS}

DEFAULT_MODEL = "gpt-4o"
MAX_TURNS = 10  # hard cap on tool-call rounds to bound cost + latency


@dataclass
class TraceEvent:
    """One step of the agent's reasoning, as it happened."""

    kind: str  # 'tool_call' | 'tool_result' | 'tool_error' | 'message' | 'final'
    name: str | None = None
    args: dict[str, Any] | None = None
    result: Any = None
    error: str | None = None
    content: str | None = None


@dataclass
class AgentResult:
    answer: str  # the final markdown the agent produced
    trace: list[TraceEvent] = field(default_factory=list)
    turns: int = 0


def _dispatch_tool(name: str, args_json: str) -> tuple[Any, str | None]:
    """Run one tool call. Returns (result, error_message_or_None)."""
    fn = TOOL_TABLE.get(name)
    if fn is None:
        return None, f"unknown tool: {name}"
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as e:
        return None, f"malformed JSON args: {e}"
    try:
        return fn(**args), None
    except Exception as e:
        log.exception("tool %s failed", name)
        return None, f"{type(e).__name__}: {e}"


def run_agent(
    query: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT_V1,
    max_turns: int = MAX_TURNS,
    client: OpenAI | None = None,
) -> AgentResult:
    """Run the agent loop end-to-end and return the answer + trace.

    Use ``stream_agent`` instead when you want to surface intermediate
    events live (e.g. SSE in the playground).
    """
    result = AgentResult(answer="")
    for ev in stream_agent(
        query,
        model=model,
        system_prompt=system_prompt,
        max_turns=max_turns,
        client=client,
    ):
        result.trace.append(ev)
        if ev.kind == "final":
            result.answer = ev.content or ""
    result.turns = sum(1 for ev in result.trace if ev.kind == "tool_call")
    return result


def stream_agent(
    query: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT_V1,
    max_turns: int = MAX_TURNS,
    client: OpenAI | None = None,
) -> Iterator[TraceEvent]:
    """Yield TraceEvents as they happen. End with a 'final' event."""
    if client is None:
        # Only touch env-backed Settings if we actually need to build a real
        # client — lets unit tests inject a fake without setting MP/OpenAI keys.
        client = OpenAI(api_key=get_settings().openai_api_key)
    tools = tools_to_openai_specs(ALL_TOOLS)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    for turn in range(max_turns):
        log.debug("turn %d → openai", turn)
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            parallel_tool_calls=True,
        )
        choice = response.choices[0]
        msg = choice.message

        # No tool calls → final answer.
        if not msg.tool_calls:
            answer = msg.content or ""
            yield TraceEvent(kind="final", content=answer)
            return

        # Record the assistant message verbatim so OpenAI can match tool_call_ids.
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,  # type: ignore[union-attr]
                            "arguments": tc.function.arguments,  # type: ignore[union-attr]
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        # Emit tool_call events first (so the UI sees them all at once),
        # then dispatch in parallel. Each MP/cache call holds the GIL only
        # briefly; the long wait is the HTTP round-trip, so threads scale.
        planned: list[tuple[Any, str, str, dict[str, Any]]] = []
        for tc in msg.tool_calls:
            name = tc.function.name  # type: ignore[union-attr]
            args_json = tc.function.arguments  # type: ignore[union-attr]
            try:
                args_pretty = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args_pretty = {"_raw": args_json}
            yield TraceEvent(kind="tool_call", name=name, args=args_pretty)
            planned.append((tc, name, args_json, args_pretty))

        # Fan out — but keep results in OpenAI's original order so tool_call_id
        # alignment in `messages` stays correct.
        if len(planned) > 1:
            with ThreadPoolExecutor(max_workers=min(len(planned), 5)) as pool:
                outcomes = list(pool.map(lambda p: _dispatch_tool(p[1], p[2]), planned))
        else:
            outcomes = [_dispatch_tool(planned[0][1], planned[0][2])]

        for (tc, name, _args_json, _args_pretty), (result, error) in zip(
            planned, outcomes, strict=True
        ):
            if error is not None:
                yield TraceEvent(kind="tool_error", name=name, error=error)
                tool_message_content = json.dumps({"error": error})
            else:
                yield TraceEvent(kind="tool_result", name=name, result=result)
                tool_message_content = json.dumps(result, default=str)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_message_content,
                }
            )

    yield TraceEvent(
        kind="final",
        content=(
            "Reached the maximum number of tool-calling rounds without converging. "
            "Partial results above — try a more specific query or fewer "
            "constraints."
        ),
    )
