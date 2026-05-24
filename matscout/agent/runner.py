"""OpenAI Responses API agent loop for matscout, backed by remote MCP.

The agent receives a natural-language query and runs through gpt-4o via the
Responses API. The 10 typed tools are not handed to OpenAI as JSON-schemas
here — instead, OpenAI connects to our hosted MCP server (the same one
mounted on this uvicorn process at ``/mcp/http/``) and discovers them via
``tools/list``. Every tool call OpenAI decides to make is then dispatched
over HTTP back to our MCP server, which executes the underlying Python
function and returns the result. This makes the "MCP server" claim
verifiable from the browser: a recruiter watching DevTools will see HTTP
hits from OpenAI's egress to ``matscout.prfo.design/mcp/http/`` on every
query, not a synthetic loop inside our Python.

Self-correction stays prompt-driven (see ``agent/prompts.py``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from matscout.agent.prompts import SYSTEM_PROMPT_V1
from matscout.config import get_settings

log = logging.getLogger("matscout.agent")

DEFAULT_MODEL = "gpt-4o"
# URL the OpenAI Responses API will call as a remote MCP server. Must be
# publicly reachable from OpenAI's egress; in dev you'd point this at an
# ngrok tunnel or a deployed instance.
DEFAULT_MCP_SERVER_URL = "https://matscout.prfo.design/mcp/http/"
DEFAULT_MCP_SERVER_LABEL = "matscout"


@dataclass
class TraceEvent:
    """One step of the agent's reasoning, as it happened.

    Shape preserved across the chat.completions → Responses API migration
    so the existing polling UI doesn't need to change. ``kind="tool_call"``
    is emitted for ``mcp_call`` items; ``kind="tool_result"`` for the
    completion of one.
    """

    kind: str  # 'thinking' | 'tool_call' | 'tool_result' | 'tool_error' | 'final'
    name: str | None = None
    args: dict[str, Any] | None = None
    result: Any = None
    error: str | None = None
    content: str | None = None
    turn: int | None = None
    # For Responses API conversations we use OpenAI's server-side state
    # (previous_response_id) rather than passing the full message list
    # back ourselves. The terminal 'final' event surfaces the just-issued
    # response_id so the web layer can park it against the conversation.
    response_id: str | None = None
    # Kept for backwards compatibility with the old runner / snapshot
    # resume code — populated only when reconstructing from a snapshot.
    state_messages: list[dict[str, Any]] | None = None


@dataclass
class AgentResult:
    answer: str
    trace: list[TraceEvent] = field(default_factory=list)
    turns: int = 0


def run_agent(
    query: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT_V1,
    client: OpenAI | None = None,
    mcp_server_url: str = DEFAULT_MCP_SERVER_URL,
) -> AgentResult:
    """Run the agent end-to-end and return the final answer + trace.

    Use ``stream_agent`` instead when you want intermediate events live
    (e.g. for polling clients).
    """
    result = AgentResult(answer="")
    for ev in stream_agent(
        query,
        model=model,
        system_prompt=system_prompt,
        client=client,
        mcp_server_url=mcp_server_url,
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
    client: OpenAI | None = None,
    previous_response_id: str | None = None,
    mcp_server_url: str = DEFAULT_MCP_SERVER_URL,
    mcp_server_label: str = DEFAULT_MCP_SERVER_LABEL,
    prior_messages: list[dict[str, Any]] | None = None,
) -> Iterator[TraceEvent]:
    """Yield TraceEvents in real time. End with a 'final' event.

    Conversation continuation has two modes:

    1. ``previous_response_id`` — OpenAI server-side state, the preferred
       path for live multi-turn conversations. Subsequent turns refer to
       the previous response and OpenAI carries the prior context.
    2. ``prior_messages`` — legacy in-process state, used when resuming
       from a /r/{id} snapshot whose previous_response_id is no longer
       valid (OpenAI expires response IDs after their retention window).
       In that case we cram the reconstructed messages into ``input`` and
       start a fresh response chain.
    """
    if client is None:
        api_key = get_settings().openai_api_key
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. The web playground uses gpt-4o "
                "for agent reasoning; please set OPENAI_API_KEY in the environment. "
                "(MCP usage via Claude Desktop / Code does not need this key.)"
            )
        client = OpenAI(api_key=api_key)

    # Build the `input` payload. Two shapes accepted by Responses API:
    # plain string (simplest), or a list of message-like items (richer).
    input_payload: Any
    if prior_messages:
        # Snapshot resume — flatten reconstructed messages into Responses
        # input items. Tool exchanges from chat.completions don't map
        # cleanly to Responses items, so we collapse everything to text
        # role messages — the agent loses fine-grained turn structure but
        # keeps the conversational gist.
        items: list[dict[str, Any]] = []
        for m in prior_messages:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "system":
                # System prompts go in the `instructions` field, not input.
                continue
            if role in ("user", "assistant") and content:
                items.append({"role": role, "content": content})
        items.append({"role": "user", "content": query})
        input_payload = items
    else:
        input_payload = query

    # OpenAI SDK's typed Iterable[FunctionToolParam | ...] doesn't yet
    # include the "mcp" tool variant in its public TypedDict union — the
    # SDK still accepts it at runtime, so we silence the type checker
    # with a cast rather than redefining the upstream type.
    tools_param: Any = [
        {
            "type": "mcp",
            "server_label": mcp_server_label,
            "server_url": mcp_server_url,
            "require_approval": "never",
        }
    ]

    yield TraceEvent(
        kind="thinking",
        turn=1,
        content="Planning next step (via MCP)…",
    )

    # Streaming pass. We track partial items by output_index so a
    # tool_call event fires the moment the model starts calling a tool,
    # and the corresponding tool_result fires when that item finishes.
    pending_calls: dict[int, dict[str, Any]] = {}
    answer_parts: list[str] = []
    response_id: str | None = None
    saw_first_text = False

    try:
        stream = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=input_payload,
            tools=tools_param,
            previous_response_id=previous_response_id,
            stream=True,
        )
    except Exception as e:
        log.exception("responses.create failed")
        yield TraceEvent(kind="tool_error", error=f"{type(e).__name__}: {e}")
        yield TraceEvent(
            kind="final",
            content=(
                "The agent loop failed to start. This usually means the OpenAI "
                "Responses API couldn't reach the MCP server, or the request "
                "was malformed. Details above."
            ),
        )
        return

    for event in stream:
        etype = getattr(event, "type", "")
        if etype == "response.created":
            response_id = getattr(getattr(event, "response", None), "id", None)

        elif etype == "response.output_item.added":
            item = getattr(event, "item", None)
            idx = getattr(event, "output_index", None)
            if item is None or idx is None:
                continue
            it_type = getattr(item, "type", None)
            if it_type == "mcp_call":
                name = getattr(item, "name", "?")
                pending_calls[idx] = {"name": name, "args_json": ""}
                # Args may stream in via mcp_call_arguments.delta — for
                # the polling UI we emit tool_call now with an empty
                # args dict; the args themselves will appear in the
                # later tool_result. (The current UI shows args from
                # tool_call but if they're missing it just shows the
                # name, which is acceptable.)
                yield TraceEvent(kind="tool_call", name=name, args={})

        elif etype == "response.mcp_call_arguments.delta":
            # Concatenate streamed arg fragments into the pending call
            # so we can attach them to the tool_result.
            idx = getattr(event, "output_index", None)
            delta = getattr(event, "delta", "") or ""
            if idx is not None and idx in pending_calls:
                pending_calls[idx]["args_json"] += delta

        elif etype == "response.output_item.done":
            item = getattr(event, "item", None)
            idx = getattr(event, "output_index", None)
            if item is None or idx is None:
                continue
            it_type = getattr(item, "type", None)
            if it_type == "mcp_call":
                name = getattr(item, "name", "?")
                output_str = getattr(item, "output", None) or ""
                err = getattr(item, "error", None)
                pending = pending_calls.pop(idx, {"args_json": ""})
                # args might come from the item itself (final), fall
                # back to the streamed accumulation.
                args_str = getattr(item, "arguments", None) or pending.get("args_json", "") or ""
                try:
                    args_obj = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args_obj = {"_raw": args_str}
                try:
                    result_obj: Any = json.loads(output_str) if output_str else None
                except json.JSONDecodeError:
                    result_obj = output_str

                if err:
                    yield TraceEvent(
                        kind="tool_error",
                        name=name,
                        args=args_obj,
                        error=str(err),
                    )
                else:
                    yield TraceEvent(
                        kind="tool_result",
                        name=name,
                        args=args_obj,
                        result=result_obj,
                    )
            elif it_type == "message":
                # Message item finishing — the text was already streamed
                # via output_text.delta into answer_parts; no extra event
                # needed here.
                pass

        elif etype == "response.output_text.delta":
            delta = getattr(event, "delta", "") or ""
            if delta:
                if not saw_first_text:
                    saw_first_text = True
                    yield TraceEvent(
                        kind="thinking",
                        turn=2,
                        content="Composing answer…",
                    )
                answer_parts.append(delta)

        elif etype == "response.completed":
            final_text = "".join(answer_parts)
            yield TraceEvent(kind="final", content=final_text, response_id=response_id)
            return

        elif etype in {"response.failed", "response.incomplete"}:
            err_obj = getattr(event, "response", None)
            err_msg = "Response failed"
            if err_obj is not None and getattr(err_obj, "error", None):
                err_msg = f"{err_msg}: {err_obj.error}"
            yield TraceEvent(kind="tool_error", error=err_msg)
            yield TraceEvent(
                kind="final",
                content="(The Responses API returned an incomplete or failed result.)",
                response_id=response_id,
            )
            return

    # If the stream ended without an explicit response.completed (rare),
    # still emit a final so the polling client doesn't hang.
    yield TraceEvent(
        kind="final",
        content="".join(answer_parts) or "(stream ended without a completed response)",
        response_id=response_id,
    )
