"""Invariants for the Pydantic models - these are the agent's API surface."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matscout.models import (
    Candidate,
    ComparisonRow,
    ComparisonTable,
    CrystalSystem,
    Material,
    SearchFilters,
    StabilityReport,
    Symmetry,
    Verdict,
)


# ---- Verdict thresholds ----
@pytest.mark.parametrize(
    "e, expected",
    [
        (0.0, Verdict.STABLE),
        (-0.001, Verdict.STABLE),  # numerical noise around zero
        (0.020, Verdict.METASTABLE),
        (0.025, Verdict.METASTABLE),
        (0.026, Verdict.UNSTABLE),
        (1.5, Verdict.UNSTABLE),
        (None, Verdict.UNSTABLE),
    ],
)
def test_verdict_thresholds(e: float | None, expected: Verdict) -> None:
    assert Verdict.from_e_above_hull(e) is expected


def test_verdict_explanation_mentions_mev() -> None:
    v = Verdict.from_e_above_hull(0.018)
    assert "meV/atom" in v.explain(0.018)


def test_verdict_unknown_when_no_data() -> None:
    v = Verdict.from_e_above_hull(None)
    assert v is Verdict.UNSTABLE
    assert "unavailable" in v.explain(None).lower()


# ---- SearchFilters validation ----
def test_search_filters_normalises_elements() -> None:
    f = SearchFilters(elements=["si", "  o "])
    assert f.elements == ["Si", "O"]


def test_search_filters_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(band_gap_range=(2.0, 1.0))


def test_search_filters_default_limit_is_50() -> None:
    assert SearchFilters().limit == 50


def test_search_filters_rejects_negative_e_above_hull() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(max_energy_above_hull=-0.1)


def test_search_filters_extra_keys_forbidden() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(unknown_field=123)  # type: ignore[call-arg]


# ---- Candidate / Material ----
def test_candidate_minimal() -> None:
    c = Candidate(
        material_id="mp-149",
        formula_pretty="Si",
        elements=["Si"],
        nelements=1,
    )
    assert c.material_id == "mp-149"
    assert c.verdict is Verdict.UNSTABLE  # no e_above_hull → unknown


def test_candidate_verdict_from_e_hull() -> None:
    c = Candidate(
        material_id="mp-149",
        formula_pretty="Si",
        elements=["Si"],
        nelements=1,
        energy_above_hull=0.0,
    )
    assert c.verdict is Verdict.STABLE


def test_material_full_inflate_and_dump() -> None:
    m = Material(
        material_id="mp-149",
        formula_pretty="Si",
        elements=["Si"],
        nelements=1,
        band_gap=0.61,
        density=2.33,
        energy_above_hull=0.0,
        is_stable=True,
        symmetry=Symmetry(
            crystal_system=CrystalSystem.CUBIC,
            spacegroup_symbol="Fd-3m",
            spacegroup_number=227,
        ),
    )
    d = m.model_dump(mode="json")
    assert d["symmetry"]["crystal_system"] == "Cubic"
    assert d["material_id"] == "mp-149"


# ---- Symmetry ----
def test_symmetry_rejects_invalid_spacegroup() -> None:
    with pytest.raises(ValidationError):
        Symmetry(spacegroup_number=231)
    with pytest.raises(ValidationError):
        Symmetry(spacegroup_number=0)


# ---- ComparisonTable ----
def test_comparison_table_roundtrip() -> None:
    t = ComparisonTable(
        properties=["band_gap", "density"],
        rows=[
            ComparisonRow(
                material_id="mp-149",
                formula_pretty="Si",
                values={"band_gap": 0.61, "density": 2.33},
            ),
            ComparisonRow(
                material_id="mp-2534",
                formula_pretty="GaAs",
                values={"band_gap": 1.42, "density": 5.32},
            ),
        ],
    )
    assert len(t.rows) == 2
    json = t.model_dump_json()
    again = ComparisonTable.model_validate_json(json)
    assert again == t


# ---- StabilityReport ----
def test_stability_report_explains_metastable() -> None:
    r = StabilityReport(
        material_id="mp-x",
        formula_pretty="X",
        energy_above_hull=0.018,
        is_stable=False,
        verdict=Verdict.METASTABLE,
        explanation=Verdict.METASTABLE.explain(0.018),
    )
    assert "18 meV/atom" in r.explanation
