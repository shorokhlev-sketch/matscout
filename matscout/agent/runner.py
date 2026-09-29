"""OpenAI Responses API agent loop for matscout, backed by remote MCP.

The agent receives a natural-language query and runs through gpt-4o via the
Responses API. The 25 typed tools (``ALL_TOOLS`` in ``tool_facades.py``)
are not handed to OpenAI as JSON-schemas here. Instead, OpenAI connects to
our hosted MCP server (the same one mounted on this uvicorn process at
``/mcp/http/``) and discovers them via ``tools/list``. Every tool call
OpenAI decides to make is then dispatched over HTTP back to our MCP server,
which executes the underlying Python function and returns the result. The
MCP path is the production path: every query from the web playground
produces HTTP hits from OpenAI's egress to ``DEFAULT_MCP_SERVER_URL``,
not a synthetic loop inside our Python.

Self-correction stays prompt-driven (see ``agent/prompts.py``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from matscout.agent.prompts import (
    SYSTEM_PROMPT_ANALYSIS,
    SYSTEM_PROMPT_DISCOVERY,
    SYSTEM_PROMPT_V1,
)
from matscout.config import get_settings

log = logging.getLogger("matscout.agent")

DEFAULT_MODEL = "gpt-4o"
# URL the OpenAI Responses API will call as a remote MCP server. Must be
# publicly reachable from OpenAI's egress; in dev you'd point this at an
# ngrok tunnel or a deployed instance.
DEFAULT_MCP_SERVER_URL = "https://matscout.prfo.design/mcp/http/"
DEFAULT_MCP_SERVER_LABEL = "matscout"

# --- Two-phase agent: tool allowlists per phase ---
#
# Stage 1 (Discovery) only has tools that produce candidate sets - fast,
# broad, no expensive drilling. Forces the model to STOP after finding
# candidates instead of trying to compose the final answer in one breath.
# Stage 2 (Analysis) has the wider toolkit and writes the final markdown.
_DISCOVERY_TOOL_NAMES: list[str] = [
    "search_materials",
    "find_battery_anode",
    "find_battery_cathode",
    "find_solar_absorber",
    "find_thermoelectric",
    "find_transparent_conductor",
    "find_cod_experimental",
    "optimade_search",
    "find_preprints",
    "search_openalex",
    "get_wikipedia_summary",
    # JARVIS-DFT direct tools dropped from the agent's allow-list 2026-05.
    # NIST static dumps return 502 and the agent kept calling them as a
    # general fallback, polluting the trace with "unavailable" payloads.
    # JARVIS data is still reachable on demand via
    # optimade_search(providers=["jarvis"], filter=…) for queries that
    # genuinely need it (2D / monolayer / topological).
]
_ANALYSIS_TOOL_NAMES: list[str] = [
    "get_material",
    "compare_materials",
    "check_stability",
    "get_elastic_properties",
    "get_electronic_summary",
    "get_phase_diagram",
    "compute_phase_diagram_strict",
    "predict_decomposition",
    "get_competing_phases",
    "pareto_rank",
    "get_structure",
    "get_doi_metadata",
    "find_preprints",
    "search_openalex",
    "get_wikipedia_summary",
    "find_cod_experimental",
    "optimade_search",
]


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


@dataclass
class AgentResult:
    answer: str
    trace: list[TraceEvent] = field(default_factory=list)
    turns: int = 0


def _synthesize_narration(
    name: str, args: dict[str, Any], recent_calls: list[tuple[str, dict[str, Any]]]
) -> tuple[str, str]:
    """Build a (kind, sentence) from a tool name + args.

    Used as a fallback when the model didn't write a between-tool
    narration itself. The UI shows these the same way as model-written
    narrations, so a non-technical viewer always sees a human-readable
    sentence per agent decision instead of a bare function call.

    Also detects the "model just retried the same tool with looser
    args" pattern and emits an ``adaptation`` event for it.
    """
    # Adaptation detection: same tool name as the previous call, with
    # a wider band-gap or e-above-hull window, OR with `only_stable`
    # dropped, OR with `limit` bumped.
    if recent_calls:
        prev_name, prev_args = recent_calls[-1]
        if prev_name == name:
            change = _diff_for_adaptation(prev_args, args)
            if change:
                return "adaptation", f"Adapting: {change}"

    if name == "search_materials":
        return "reasoning", _summarize_search(args)
    if name == "get_material":
        mid = args.get("material_id", "?")
        return "reasoning", f"Pulling full property sheet for {mid}."
    if name == "check_stability":
        mid = args.get("material_id", "?")
        return "reasoning", f"Checking convex-hull position for {mid}."
    if name == "compare_materials":
        ids = args.get("material_ids", []) or []
        return "reasoning", f"Building side-by-side comparison of {len(ids)} candidates."
    if name == "get_phase_diagram":
        cs = args.get("chemsys", "?")
        return "reasoning", f"Loading the {cs} phase diagram."
    if name == "predict_decomposition":
        mid = args.get("material_id", "?")
        return "reasoning", f"Predicting decomposition products of {mid}."
    if name == "get_competing_phases":
        f = args.get("formula", "?")
        return "reasoning", f"Looking for competing phases around {f}."
    if name == "get_structure":
        mid = args.get("material_id", "?")
        fmt = args.get("fmt", "cif").upper()
        return "reasoning", f"Exporting {mid} crystal structure as {fmt}."
    if name == "get_doi_metadata":
        doi = args.get("doi", "?")
        return "reasoning", f"Resolving DOI {doi} via CrossRef."
    if name == "find_preprints":
        q = (args.get("query") or "?")[:50]
        return "reasoning", f'Searching arXiv preprints for "{q}".'
    return "reasoning", f"Calling {name}."


def _summarize_search(args: dict[str, Any]) -> str:
    parts: list[str] = []
    if (bg := args.get("band_gap_range")) and isinstance(bg, list | tuple) and len(bg) == 2:
        parts.append(f"band gap {bg[0]}-{bg[1]} eV")
    if (els := args.get("elements")) and isinstance(els, list):
        parts.append(f"containing {', '.join(els)}")
    if (xe := args.get("exclude_elements")) and isinstance(xe, list):
        parts.append(f"excluding {', '.join(xe)}")
    if (d := args.get("density_range")) and isinstance(d, list | tuple) and len(d) == 2:
        parts.append(f"density {d[0]}-{d[1]} g/cm³")
    if args.get("only_stable"):
        parts.append("stable only")
    if args.get("is_metal") is True:
        parts.append("metallic")
    if args.get("is_metal") is False:
        parts.append("non-metallic")
    if (n := args.get("num_elements")) is not None:
        parts.append(f"{n}-element systems")
    descriptor = ", ".join(parts) if parts else "broad filter"
    return f"Searching the Materials Project for candidates ({descriptor})."


def _diff_for_adaptation(prev: dict[str, Any], curr: dict[str, Any]) -> str | None:
    """Detect the meaningful 'relaxation' between two consecutive calls."""

    def _width(r: Any) -> float | None:
        if isinstance(r, list | tuple) and len(r) == 2:
            try:
                return float(r[1]) - float(r[0])
            except (TypeError, ValueError):
                return None
        return None

    # Band-gap window widened
    pw = _width(prev.get("band_gap_range"))
    cw = _width(curr.get("band_gap_range"))
    if pw is not None and cw is not None and cw > pw + 0.05:
        pbg = prev.get("band_gap_range")
        cbg = curr.get("band_gap_range")
        return f"got too few hits, widening band gap window from {pbg} to {cbg}."

    # Density window widened
    pw = _width(prev.get("density_range"))
    cw = _width(curr.get("density_range"))
    if pw is not None and cw is not None and cw > pw + 0.1:
        return (
            f"widening density window from {prev.get('density_range')} "
            f"to {curr.get('density_range')}."
        )

    # only_stable dropped
    if prev.get("only_stable") and not curr.get("only_stable"):
        return "no stable hits, allowing metastable phases this time."

    # max_energy_above_hull raised
    pmh = prev.get("max_energy_above_hull")
    cmh = curr.get("max_energy_above_hull")
    if isinstance(pmh, int | float) and isinstance(cmh, int | float) and cmh > pmh:
        return f"raising the energy_above_hull cap from {pmh} to {cmh} eV/atom."

    # limit lowered (tightening) - heuristic for >50 hit case
    pl = prev.get("limit")
    cl = curr.get("limit")
    if isinstance(pl, int) and isinstance(cl, int) and cl < pl // 2:
        return f"too many hits, tightening limit from {pl} to {cl}."

    return None


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
    locale_hint: str = "",
    client: OpenAI | None = None,
    previous_response_id: str | None = None,
    mcp_server_url: str = DEFAULT_MCP_SERVER_URL,
    mcp_server_label: str = DEFAULT_MCP_SERVER_LABEL,
    prior_messages: list[dict[str, Any]] | None = None,
    single_phase: bool = False,
) -> Iterator[TraceEvent]:
    """Two-phase orchestrator over OpenAI Responses + remote MCP.

    Phase 1 (Discovery) runs with a narrow tool subset (search-class
    only) and produces a candidate set. Phase 2 (Analysis) takes those
    candidates as input and runs with the wider toolkit to drill in,
    compare, cross-validate, rank, and compose the final answer.

    Why two phases instead of one big call: gpt-4o under a single call
    hits its tool-call budget around 5-7 invocations into a deep-research
    workflow and then ends without composing the final markdown. Splitting
    gives each phase its own budget and its own focused instructions,
    which produces a defensible final answer significantly more reliably.

    ``single_phase=True`` falls back to the legacy one-call path. Used
    when resuming a /r/{id} snapshot (no point paying for two calls when
    we're just continuing an existing chain).
    """
    if client is None:
        api_key = get_settings().openai_api_key
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. The web playground uses gpt-4o "
                "for agent reasoning; please set OPENAI_API_KEY in the environment."
            )
        client = OpenAI(api_key=api_key)

    # Snapshot resume → single-phase fallback. No discovery needed; the
    # restored message list already contains "what we know so far".
    if prior_messages or single_phase:
        yield from _stream_phase(
            query=query,
            instructions=system_prompt + locale_hint,
            allowed_tool_names=None,
            max_tool_calls=25,
            client=client,
            previous_response_id=previous_response_id,
            mcp_server_url=mcp_server_url,
            mcp_server_label=mcp_server_label,
            prior_messages=prior_messages,
            phase_label="single",
        )
        return

    # --- Phase 1: Discovery ---
    yield TraceEvent(
        kind="phase",
        turn=1,
        content="Discovery: finding candidate materials",
    )

    stage1_text = ""
    stage1_response_id: str | None = None
    for ev in _stream_phase(
        query=query,
        instructions=SYSTEM_PROMPT_DISCOVERY + locale_hint,
        allowed_tool_names=_DISCOVERY_TOOL_NAMES,
        max_tool_calls=10,
        client=client,
        previous_response_id=previous_response_id,
        mcp_server_url=mcp_server_url,
        mcp_server_label=mcp_server_label,
        phase_label="discovery",
    ):
        if ev.kind == "final":
            stage1_text = ev.content or ""
            stage1_response_id = ev.response_id
            # DON'T forward this 'final' - it's the discovery summary,
            # not the user-facing answer. The actual final comes from
            # Phase 2 below.
        else:
            yield ev

    # --- Phase 2: Analysis ---
    yield TraceEvent(
        kind="phase",
        turn=2,
        content="Analysis: drilling in, ranking, composing answer",
    )

    analysis_input = (
        f"User query: {query}\n\n"
        f"Discovery phase findings:\n{stage1_text}\n\n"
        "Drill in: pull full property sheets in parallel for the top "
        "candidates above, compare them, cross-validate ONE non-trivial "
        "claim against a second source, run pareto_rank if you have ≥3 "
        "candidates with ≥2 competing properties, then write the FINAL "
        "markdown answer with a ranked table + rationale + trade-offs."
    )
    yield from _stream_phase(
        query=analysis_input,
        instructions=SYSTEM_PROMPT_ANALYSIS + locale_hint,
        allowed_tool_names=_ANALYSIS_TOOL_NAMES,
        max_tool_calls=25,
        client=client,
        previous_response_id=stage1_response_id,
        mcp_server_url=mcp_server_url,
        mcp_server_label=mcp_server_label,
        phase_label="analysis",
    )


def _stream_phase(
    *,
    query: str,
    instructions: str,
    allowed_tool_names: list[str] | None,
    max_tool_calls: int,
    client: OpenAI,
    model: str = DEFAULT_MODEL,
    previous_response_id: str | None = None,
    mcp_server_url: str = DEFAULT_MCP_SERVER_URL,
    mcp_server_label: str = DEFAULT_MCP_SERVER_LABEL,
    prior_messages: list[dict[str, Any]] | None = None,
    phase_label: str = "single",
) -> Iterator[TraceEvent]:
    """One Responses API call, streaming events. Internal helper for ``stream_agent``.

    ``allowed_tool_names`` is forwarded to the MCP tool spec as
    ``allowed_tools`` - restricts the model to a subset of the 25 tools
    on our MCP server. None = all tools allowed.

    Conversation continuation has two modes:

    1. ``previous_response_id`` - OpenAI server-side state, the preferred
       path for live multi-turn conversations.
    2. ``prior_messages`` - legacy in-process state, used when resuming
       from a /r/{id} snapshot whose previous_response_id is no longer
       valid.
    """
    # Build the `input` payload. Two shapes accepted by Responses API:
    # plain string (simplest), or a list of message-like items (richer).
    input_payload: Any
    if prior_messages:
        # Snapshot resume - flatten reconstructed messages into Responses
        # input items. Tool exchanges from chat.completions don't map
        # cleanly to Responses items, so we collapse everything to text
        # role messages - the agent loses fine-grained turn structure but
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
    # include the "mcp" tool variant in its public TypedDict union - the
    # SDK still accepts it at runtime, so we silence the type checker
    # with a cast rather than redefining the upstream type.
    mcp_tool_spec: dict[str, Any] = {
        "type": "mcp",
        "server_label": mcp_server_label,
        "server_url": mcp_server_url,
        "require_approval": "never",
    }
    if allowed_tool_names is not None:
        # Restrict the model to a subset of the MCP server's tools.
        # OpenAI accepts ``allowed_tools`` as either a list of names or a
        # filter dict; the list form is enough for our purposes.
        mcp_tool_spec["allowed_tools"] = allowed_tool_names
    tools_param: Any = [mcp_tool_spec]

    yield TraceEvent(
        kind="thinking",
        turn=1,
        content=f"Planning next step (via MCP, phase={phase_label})…",
    )

    # Recent tool-call history - used to detect "the model just retried
    # the same tool with widened args", which deserves an adaptation
    # event even when the model didn't write a narration itself.
    recent_calls: list[tuple[str, dict[str, Any]]] = []

    # --- Streaming pass ---
    #
    # We translate OpenAI Responses stream events to our TraceEvent shape
    # in real time. Three pieces of state worth knowing about:
    #
    # 1. ``pending_calls`` accumulates streamed argument deltas per
    #    output_index for each mcp_call item, so we can populate the
    #    `tool_call` event with full args at the moment the call is
    #    actually dispatched (rather than firing tool_call with empty
    #    args on output_item.added before the args arrive).
    #
    # 2. ``message_texts`` accumulates per-output_index text fragments.
    #    Responses can produce several `message` items in a single
    #    response - intermediate ones are narrations between tool calls
    #    (the system prompt instructs the model to do this), the very
    #    last one is the final answer. We don't know during streaming
    #    which message is the last, so we use one-message lookahead
    #    (``held_message_idx``) - a finished message is held until the
    #    NEXT activity (another item.added, another item.done, or
    #    response.completed) confirms whether it was narration or final.
    #
    # 3. ``response_id`` is captured up front so the terminal `final`
    #    event can carry it back for conversation continuation.
    pending_calls: dict[int, dict[str, Any]] = {}
    message_texts: dict[int, str] = {}
    # Track which message indices we've already emitted as narration -
    # at response.completed we must NOT recycle them as the final
    # answer, otherwise a model that wrote narration + tool calls but
    # forgot to compose a real synthesis ends up with the narration
    # echoed twice (once in trace as reasoning, once as the final).
    flushed_message_idxs: set[int] = set()
    held_message_idx: int | None = None
    response_id: str | None = None

    def _classify_message(text: str) -> str:
        """'adaptation' for self-correction narrations, 'reasoning' otherwise."""
        head = text.lstrip()[:30].lower()
        if head.startswith("adapting:") or head.startswith("adapt:"):
            return "adaptation"
        return "reasoning"

    try:
        stream = client.responses.create(
            model=model,
            instructions=instructions,
            input=input_payload,
            tools=tools_param,
            previous_response_id=previous_response_id,
            max_tool_calls=max_tool_calls,
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

    def _flush_held_as_narration() -> Iterator[TraceEvent]:
        """Flush the held intermediate message as reasoning / adaptation."""
        nonlocal held_message_idx
        if held_message_idx is None:
            return
        text = (message_texts.get(held_message_idx, "") or "").strip()
        idx = held_message_idx
        held_message_idx = None
        if not text:
            return
        flushed_message_idxs.add(idx)
        kind = _classify_message(text)
        yield TraceEvent(kind=kind, content=text, turn=idx)

    for event in stream:
        etype = getattr(event, "type", "")

        if etype == "response.created":
            response_id = getattr(getattr(event, "response", None), "id", None)

        elif etype == "response.output_item.added":
            # New output item starting. If we were holding a previous
            # message item, it's now confirmed intermediate - flush it
            # as narration before processing the new item.
            yield from _flush_held_as_narration()
            item = getattr(event, "item", None)
            idx = getattr(event, "output_index", None)
            if item is None or idx is None:
                continue
            it_type = getattr(item, "type", None)
            if it_type == "mcp_call":
                name = getattr(item, "name", "?")
                pending_calls[idx] = {"name": name, "args_json": ""}
                # Don't emit tool_call here - args aren't ready yet.
                # We'll emit on mcp_call_arguments.done with full args.

        elif etype == "response.mcp_call_arguments.delta":
            idx = getattr(event, "output_index", None)
            delta = getattr(event, "delta", "") or ""
            if idx is not None and idx in pending_calls:
                pending_calls[idx]["args_json"] += delta

        elif etype == "response.mcp_call_arguments.done":
            # Args fully streamed - NOW we can emit a populated tool_call
            # event. This is the moment the call is actually dispatched
            # to the MCP server.
            idx = getattr(event, "output_index", None)
            if idx is not None and idx in pending_calls:
                pending = pending_calls[idx]
                args_str = pending.get("args_json", "")
                try:
                    args_obj = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args_obj = {"_raw": args_str}
                pending["args"] = args_obj  # remembered for tool_result later
                name = pending["name"]

                # Either flush the model's own narration (if it wrote
                # one before this call) - or, when the model went
                # straight to a tool, synthesize a deterministic
                # narration from the tool name + args. Either way the
                # human sees a sentence per agent decision.
                if held_message_idx is not None:
                    yield from _flush_held_as_narration()
                else:
                    syn_kind, syn_text = _synthesize_narration(name, args_obj, recent_calls)
                    if syn_text:
                        yield TraceEvent(kind=syn_kind, content=syn_text)

                recent_calls.append((name, args_obj))
                yield TraceEvent(kind="tool_call", name=name, args=args_obj)

        elif etype == "response.output_text.delta":
            # Stream raw text into the per-item buffer; we'll decide
            # later whether the whole item is narration or final.
            idx = getattr(event, "output_index", None)
            delta = getattr(event, "delta", "") or ""
            if idx is not None and delta:
                message_texts[idx] = message_texts.get(idx, "") + delta

        elif etype == "response.output_item.done":
            item = getattr(event, "item", None)
            idx = getattr(event, "output_index", None)
            if item is None or idx is None:
                continue
            it_type = getattr(item, "type", None)
            if it_type == "mcp_call":
                # A tool result has come back from the MCP server. Flush
                # any held narration first (it was the "thinking before
                # this call"), then emit the result.
                yield from _flush_held_as_narration()
                name = getattr(item, "name", "?")
                output_str = getattr(item, "output", None) or ""
                err = getattr(item, "error", None)
                pending = pending_calls.pop(idx, {"args_json": ""})
                args_str = getattr(item, "arguments", None) or pending.get("args_json", "")
                try:
                    args_obj = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args_obj = {"_raw": args_str}
                try:
                    result_obj: Any = json.loads(output_str) if output_str else None
                except json.JSONDecodeError:
                    result_obj = output_str

                if err:
                    yield TraceEvent(kind="tool_error", name=name, args=args_obj, error=str(err))
                else:
                    yield TraceEvent(
                        kind="tool_result", name=name, args=args_obj, result=result_obj
                    )
            elif it_type == "message":
                # Finished message - hold it; if another item follows
                # (or response.completed says it was the last), we'll
                # then classify it as narration or final.
                yield from _flush_held_as_narration()
                held_message_idx = idx

        elif etype == "response.completed":
            # End of stream. The final answer is the last *unflushed*
            # message - anything we already shipped as a reasoning event
            # must NOT be recycled as the final, otherwise an agent that
            # wrote narration + tools but forgot to compose a real
            # synthesis echoes the narration in the answer area.
            if held_message_idx is not None:
                final_text = (message_texts.get(held_message_idx, "") or "").strip()
                held_message_idx = None
            else:
                final_text = "\n\n".join(
                    (message_texts[k] or "").strip()
                    for k in sorted(message_texts.keys())
                    if k not in flushed_message_idxs
                ).strip()

            if not recent_calls and final_text and len(final_text) < 250:
                # Model ended after a single short text and zero tool
                # calls. Surface the text as narration and replace the
                # "final" with an honest note about the glitch.
                yield TraceEvent(kind="reasoning", content=final_text)
                final_text = (
                    "The agent ended its turn after planning without calling any tools. "
                    "This is a rare model glitch. Please rerun the same query."
                )
            elif recent_calls and not final_text:
                # Model called tools but never composed a synthesis
                # message - happens when it gets confused and stops
                # short. Don't lie that the narration was the answer;
                # tell the user honestly and point at the trace.
                tool_summary = ", ".join(f"`{name}`" for name, _ in recent_calls[-5:])
                final_text = (
                    "The agent called " + tool_summary + " but didn't compose a final "
                    "answer afterwards. The tool results are visible in the trace above. "
                    "Try rerunning the query, or rephrasing it with more specific "
                    "constraints (element list, property ranges, intended application)."
                )
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
    yield from _flush_held_as_narration()
    yield TraceEvent(
        kind="final",
        content="(stream ended without a completed response)",
        response_id=response_id,
    )
