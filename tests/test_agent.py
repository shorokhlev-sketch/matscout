"""Agent loop unit tests — exercise the dispatcher + trace without OpenAI.

We script the OpenAI client's responses with simple fakes; the model
itself is irrelevant here. Live evaluation of self-correction quality
lives in tests/eval/ (next step).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from matscout.agent.runner import run_agent
from matscout.cache import Cache
from matscout.tools._client import set_cache, set_client

# ── fake OpenAI client ───────────────────────────────────────────────────────


@dataclass
class _FakeToolCall:
    id: str
    function: Any


@dataclass
class _FakeFunction:
    name: str
    arguments: str


@dataclass
class _FakeMessage:
    content: str | None = None
    tool_calls: list[_FakeToolCall] | None = None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]


@dataclass
class _FakeCompletions:
    """Scripted: each call returns the next message in the queue."""

    queue: list[_FakeMessage] = field(default_factory=list)
    received_messages: list[Any] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.received_messages.append(kwargs["messages"])
        if not self.queue:
            raise AssertionError("FakeCompletions ran out of scripted responses")
        msg = self.queue.pop(0)
        return _FakeResponse(choices=[_FakeChoice(message=msg)])


@dataclass
class _FakeChat:
    completions: _FakeCompletions


@dataclass
class _FakeOpenAIClient:
    chat: _FakeChat


def _mk_tool_call(call_id: str, name: str, args: dict[str, Any]) -> _FakeToolCall:
    return _FakeToolCall(id=call_id, function=_FakeFunction(name=name, arguments=json.dumps(args)))


# ── fake MP layer reused from tools tests ───────────────────────────────────


@dataclass
class _FakeElement:
    symbol: str


@dataclass
class _FakeSymmetry:
    crystal_system: str
    symbol: str | None
    number: int | None
    point_group: str | None


@dataclass
class _FakeDoc:
    material_id: str
    formula_pretty: str
    elements: list[_FakeElement]
    nelements: int
    band_gap: float | None = None
    density: float | None = None
    energy_above_hull: float | None = None
    formation_energy_per_atom: float | None = None
    is_stable: bool | None = None
    is_metal: bool | None = None
    symmetry: _FakeSymmetry | None = None
    formula_anonymous: str | None = None
    chemsys: str | None = None
    nsites: int | None = None
    volume: float | None = None
    density_atomic: float | None = None
    is_gap_direct: bool | None = None
    is_magnetic: bool | None = None
    uncorrected_energy_per_atom: float | None = None
    bulk_modulus: dict[str, float] | None = None
    shear_modulus: dict[str, float] | None = None
    n: float | None = None
    total_magnetization: float | None = None
    theoretical: bool = False
    deprecated: bool = False


class _FakeSummary:
    def __init__(self, docs: list[_FakeDoc]) -> None:
        self.docs = docs
        self.last_kwargs: dict[str, Any] = {}

    def search(self, **kwargs: Any) -> list[_FakeDoc]:
        self.last_kwargs = kwargs
        ids = kwargs.get("material_ids")
        if ids:
            return [d for d in self.docs if d.material_id in ids]
        return self.docs


class _FakeMaterials:
    def __init__(self, docs: list[_FakeDoc]) -> None:
        self.summary = _FakeSummary(docs)


@dataclass
class _FakeMPClient:
    docs: list[_FakeDoc]

    def __post_init__(self) -> None:
        self.materials = _FakeMaterials(self.docs)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Any:
    docs = [
        _FakeDoc(
            material_id="mp-149",
            formula_pretty="Si",
            elements=[_FakeElement("Si")],
            nelements=1,
            band_gap=0.61,
            density=2.33,
            energy_above_hull=0.0,
            is_stable=True,
            is_metal=False,
            symmetry=_FakeSymmetry("Cubic", "Fd-3m", 227, "m-3m"),
        )
    ]
    set_client(_FakeMPClient(docs=docs))
    set_cache(Cache(db_path=tmp_path / "agent.db", ttl_seconds=3600))
    yield
    set_client(None)
    set_cache(None)


# ── tests ────────────────────────────────────────────────────────────────────


def _client_that_answers(*responses: _FakeMessage) -> _FakeOpenAIClient:
    return _FakeOpenAIClient(chat=_FakeChat(completions=_FakeCompletions(queue=list(responses))))


def test_agent_direct_answer_no_tools() -> None:
    """If gpt-4o decides not to call anything, we just return its text."""
    client = _client_that_answers(_FakeMessage(content="Hello.", tool_calls=None))
    result = run_agent("just say hi", client=client)
    assert result.answer == "Hello."
    assert result.turns == 0
    assert any(ev.kind == "final" for ev in result.trace)


def test_agent_invokes_tool_then_returns() -> None:
    """Two-step loop: model asks for a tool, then synthesizes an answer."""
    tc = _mk_tool_call("c1", "get_material", {"material_id": "mp-149"})
    client = _client_that_answers(
        _FakeMessage(content=None, tool_calls=[tc]),
        _FakeMessage(content="Si has gap 0.61 eV.", tool_calls=None),
    )
    result = run_agent("tell me about Si", client=client)
    assert result.answer == "Si has gap 0.61 eV."
    assert result.turns == 1
    kinds = [ev.kind for ev in result.trace]
    assert kinds == ["tool_call", "tool_result", "final"]
    call_ev = next(ev for ev in result.trace if ev.kind == "tool_call")
    assert call_ev.name == "get_material"
    assert call_ev.args == {"material_id": "mp-149"}


def test_agent_handles_tool_error_gracefully() -> None:
    """Model asks for a nonexistent id → we record the error, model gets to react."""
    tc = _mk_tool_call("c1", "get_material", {"material_id": "mp-nope"})
    client = _client_that_answers(
        _FakeMessage(content=None, tool_calls=[tc]),
        _FakeMessage(content="That id doesn't exist.", tool_calls=None),
    )
    # FakeMP returns no docs when filtered to a missing id → MaterialNotFoundError
    result = run_agent("get mp-nope", client=client)
    assert "doesn't exist" in result.answer
    err_ev = next(ev for ev in result.trace if ev.kind == "tool_error")
    assert "MaterialNotFoundError" in (err_ev.error or "")


def test_agent_passes_messages_back_to_openai_with_tool_result() -> None:
    """OpenAI needs to see the tool_call_id + tool message to chain context."""
    tc = _mk_tool_call("call-XYZ", "get_material", {"material_id": "mp-149"})
    completions = _FakeCompletions(
        queue=[
            _FakeMessage(content=None, tool_calls=[tc]),
            _FakeMessage(content="done", tool_calls=None),
        ]
    )
    client = _FakeOpenAIClient(chat=_FakeChat(completions=completions))
    run_agent("anything", client=client)

    # Second call to OpenAI must include the assistant tool_calls + tool message
    second_messages = completions.received_messages[1]
    tool_msg = next(m for m in second_messages if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "call-XYZ"
    parsed = json.loads(tool_msg["content"])
    assert parsed["material_id"] == "mp-149"


def test_agent_caps_at_max_turns() -> None:
    """If model keeps calling tools forever, we stop and report so."""
    # Script an infinite stream of tool calls; loop must bail at max_turns.
    forever = [
        _FakeMessage(
            content=None,
            tool_calls=[_mk_tool_call(f"c{i}", "get_material", {"material_id": "mp-149"})],
        )
        for i in range(20)
    ]
    client = _client_that_answers(*forever)
    result = run_agent("loop", client=client, max_turns=3)
    assert "maximum" in result.answer.lower()
    # 3 tool-call rounds attempted
    assert result.turns == 3
