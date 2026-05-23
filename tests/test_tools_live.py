"""End-to-end tests against the real Materials Project API.

All marked @pytest.mark.live → auto-skipped by conftest when env vars
are missing (so PR CI runs green without secrets, while local runs with
the key actually hit production).

Cache stays the per-process default — first run touches the API, repeats
do not. That's intentional: live tests double as smoke tests for the
cache layer.
"""

from __future__ import annotations

import pytest

from matscout.models import Verdict
from matscout.tools import (
    check_stability,
    compare_materials,
    get_material,
    search_materials,
)

pytestmark = pytest.mark.live


# ── search_materials ───────────────────────────────────────────────────────


def test_live_search_si_o_in_band_gap_window() -> None:
    """Si-O materials with band_gap in 1.0–2.0 eV should exist (sub-stoichiometric SiO_x)."""
    hits = search_materials(elements=["Si", "O"], band_gap_range=(1.0, 2.0), limit=20)
    assert len(hits) > 0, "expected at least one Si-O candidate in band-gap window"
    for c in hits:
        assert "Si" in c.elements and "O" in c.elements
        if c.band_gap is not None:
            assert 1.0 <= c.band_gap <= 2.0


def test_live_search_only_stable_filter() -> None:
    """only_stable should not return anything with e_above_hull > 0."""
    hits = search_materials(elements=["Na"], only_stable=True, limit=10)
    assert len(hits) > 0, "Na has stable phases"
    for c in hits:
        assert c.is_stable is True or (
            c.energy_above_hull is not None and c.energy_above_hull <= 0.0 + 1e-9
        )


def test_live_search_exclude_elements_works() -> None:
    """Asking for 'Si' but excluding 'O' must not return any oxides."""
    hits = search_materials(
        elements=["Si"], exclude_elements=["O"], only_stable=True, limit=10
    )
    for c in hits:
        assert "O" not in c.elements


# ── get_material ───────────────────────────────────────────────────────────


def test_live_get_material_silicon() -> None:
    """mp-149 is the canonical Si entry; band_gap ~0.6 eV (DFT-PBE underestimate)."""
    m = get_material("mp-149")
    assert m.formula_pretty == "Si"
    assert m.elements == ["Si"]
    assert m.band_gap is not None
    assert 0.3 < m.band_gap < 1.0  # DFT-PBE Si typically lands around 0.6-0.7 eV
    assert m.symmetry is not None
    assert m.symmetry.crystal_system.value == "Cubic"
    assert m.symmetry.spacegroup_number == 227  # Fd-3m


# ── compare_materials ──────────────────────────────────────────────────────


def test_live_compare_si_and_diamond() -> None:
    """Si vs diamond — well-known DFT-PBE benchmark; gaps must both be > 0."""
    table = compare_materials(["mp-149", "mp-66"])  # Si, C (diamond)
    assert len(table.rows) == 2
    assert "band_gap" in table.properties
    by_id = {r.material_id: r for r in table.rows}
    si_gap = by_id["mp-149"].values["band_gap"]
    c_gap = by_id["mp-66"].values["band_gap"]
    assert si_gap is not None and c_gap is not None
    # Diamond's gap is famously wide (~5.4 eV PBE); Si small (~0.6 eV PBE).
    assert si_gap < c_gap, "diamond gap must exceed Si gap"
    assert c_gap > 4.0, "diamond DFT-PBE gap should be > 4 eV"


# ── check_stability ────────────────────────────────────────────────────────


def test_live_silicon_is_stable() -> None:
    r = check_stability("mp-149")
    assert r.verdict is Verdict.STABLE
    assert r.is_stable is True
    assert r.energy_above_hull is not None
    assert r.energy_above_hull <= 1e-9
    assert "convex hull" in r.explanation.lower()


# ── acceptance scenarios (full chain, no agent yet) ───────────────────────


def test_live_acceptance_semiconductor_for_solar_cell() -> None:
    """Acceptance #1: 'Stable semiconductor with band gap ~1.5 eV for solar cell.'

    1.0–1.7 eV is the Shockley-Queisser sweet spot for single-junction PV.
    Tools must surface real, named candidates — not an empty list.
    """
    hits = search_materials(
        band_gap_range=(1.0, 1.7),
        only_stable=True,
        limit=20,
    )
    assert len(hits) >= 5, "expected several stable semiconductors in PV-relevant band-gap window"
    # Each surviving candidate should genuinely sit on the hull
    for c in hits:
        if c.energy_above_hull is not None:
            assert c.energy_above_hull <= 1e-9
