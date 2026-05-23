"""Offline tests for the tool layer — no network, no real MP.

Inject a mock MPRester through `tools._client.set_client()` and an in-tmp
SQLite cache through `tools._client.set_cache()`. This lets us verify
filter translation, response mapping, and cache wiring without spending
API quota.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from matscout.cache import Cache
from matscout.models import Candidate, SearchFilters
from matscout.tools import search_materials
from matscout.tools._client import set_cache, set_client

# ── tiny fixture machinery — mock MPRester ──────────────────────────────────


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


class _FakeSummary:
    """Captures the kwargs that the tool layer passes to search()."""

    def __init__(self, docs: list[_FakeDoc]) -> None:
        self.docs = docs
        self.last_kwargs: dict[str, Any] = {}
        self.call_count = 0

    def search(self, **kwargs: Any) -> list[_FakeDoc]:
        self.last_kwargs = kwargs
        self.call_count += 1
        return self.docs


class _FakeMaterials:
    def __init__(self, docs: list[_FakeDoc]) -> None:
        self.summary = _FakeSummary(docs)


@dataclass
class _FakeClient:
    docs: list[_FakeDoc] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.materials = _FakeMaterials(self.docs)


def _sample_docs() -> list[_FakeDoc]:
    return [
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
        ),
        _FakeDoc(
            material_id="mp-2534",
            formula_pretty="GaAs",
            elements=[_FakeElement("Ga"), _FakeElement("As")],
            nelements=2,
            band_gap=1.42,
            density=5.32,
            energy_above_hull=0.0,
            is_stable=True,
            symmetry=_FakeSymmetry("Cubic", "F-43m", 216, "-43m"),
        ),
    ]


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path) -> Any:
    """Each test gets a fresh client + fresh tmp-dir cache."""
    set_client(_FakeClient(_sample_docs()))
    set_cache(Cache(db_path=tmp_path / "cache.db", ttl_seconds=3600))
    yield
    set_client(None)
    set_cache(None)


# ── filter translation ──────────────────────────────────────────────────────


def test_search_passes_elements_to_mp() -> None:
    out = search_materials(SearchFilters(elements=["Si", "O"]))
    assert len(out) == 2  # both fake docs returned
    from matscout.tools._client import get_client

    client = get_client()
    assert client.materials.summary.last_kwargs["elements"] == ["Si", "O"]  # type: ignore[attr-defined]


def test_search_translates_only_stable_to_is_stable_true() -> None:
    search_materials(SearchFilters(only_stable=True))
    from matscout.tools._client import get_client

    kw = get_client().materials.summary.last_kwargs  # type: ignore[attr-defined]
    assert kw["is_stable"] is True
    assert "energy_above_hull" not in kw  # NOT translated to range


def test_search_translates_max_e_above_hull_to_range() -> None:
    search_materials(SearchFilters(max_energy_above_hull=0.05))
    from matscout.tools._client import get_client

    kw = get_client().materials.summary.last_kwargs  # type: ignore[attr-defined]
    assert kw["energy_above_hull"] == (0.0, 0.05)


def test_search_passes_band_gap_range() -> None:
    search_materials(SearchFilters(band_gap_range=(1.0, 2.0)))
    from matscout.tools._client import get_client

    kw = get_client().materials.summary.last_kwargs  # type: ignore[attr-defined]
    assert kw["band_gap"] == (1.0, 2.0)


def test_search_does_not_pass_unset_filters() -> None:
    search_materials(SearchFilters(elements=["Si"]))
    from matscout.tools._client import get_client

    kw = get_client().materials.summary.last_kwargs  # type: ignore[attr-defined]
    assert "band_gap" not in kw
    assert "density" not in kw
    assert "is_metal" not in kw


def test_search_accepts_kwargs_form() -> None:
    """Agent layer will pass plain kwargs (gpt tool-call format)."""
    out = search_materials(elements=["Si"], band_gap_range=(1.0, 2.0))
    assert len(out) >= 1


# ── doc → Candidate mapping ─────────────────────────────────────────────────


def test_candidate_mapping_normalizes_elements() -> None:
    out = search_materials(SearchFilters(elements=["Si"]))
    si = next(c for c in out if c.material_id == "mp-149")
    assert si.elements == ["Si"]
    assert si.symmetry is not None
    assert si.symmetry.crystal_system.value == "Cubic"
    assert si.symmetry.spacegroup_number == 227


def test_returned_objects_are_pydantic_candidates() -> None:
    out = search_materials(SearchFilters(elements=["Si"]))
    assert all(isinstance(c, Candidate) for c in out)


def test_limit_truncates_response() -> None:
    out = search_materials(SearchFilters(elements=["Si"], limit=1))
    assert len(out) == 1


# ── cache behaviour ─────────────────────────────────────────────────────────


def test_cache_hit_skips_api() -> None:
    from matscout.tools._client import get_client

    client = get_client()

    search_materials(SearchFilters(elements=["Si"]))
    assert client.materials.summary.call_count == 1  # type: ignore[attr-defined]

    search_materials(SearchFilters(elements=["Si"]))
    assert client.materials.summary.call_count == 1  # type: ignore[attr-defined]


def test_cache_keys_differ_per_args() -> None:
    from matscout.tools._client import get_client

    client = get_client()

    search_materials(SearchFilters(elements=["Si"]))
    search_materials(SearchFilters(elements=["O"]))
    assert client.materials.summary.call_count == 2  # type: ignore[attr-defined]


def test_cache_returns_equivalent_candidates() -> None:
    first = search_materials(SearchFilters(elements=["Si"]))
    second = search_materials(SearchFilters(elements=["Si"]))
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]
