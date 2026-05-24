"""Flat-kwarg facades over the typed ``tools/*`` functions.

Both the MCP server and the OpenAI agent want their tools described in
flat JSON-schema (primitives and arrays only — no nested Pydantic models).
Underneath, the typed Pydantic-driven implementations are the source of
truth. These facades:

- Accept plain kwargs from the LLM / MCP client.
- Build the strict Pydantic input model where one exists (SearchFilters).
- Return ``dict`` (JSON-serializable Pydantic dumps) so the wire format
  is consistent across surfaces.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from matscout.models import SearchFilters
from matscout.tools import (
    check_stability as _check_stability,
)
from matscout.tools import (
    compare_materials as _compare_materials,
)
from matscout.tools import (
    get_competing_phases as _get_competing_phases,
)
from matscout.tools import (
    get_material as _get_material,
)
from matscout.tools import (
    get_phase_diagram as _get_phase_diagram,
)
from matscout.tools import (
    predict_decomposition as _predict_decomposition,
)
from matscout.tools import (
    search_materials as _search_materials,
)


def search_materials(
    elements: list[str] | None = None,
    exclude_elements: list[str] | None = None,
    formula: str | None = None,
    band_gap_range: tuple[float, float] | None = None,
    density_range: tuple[float, float] | None = None,
    max_energy_above_hull: float | None = None,
    only_stable: bool = False,
    num_elements: int | None = None,
    is_metal: bool | None = None,
    is_gap_direct: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search Materials Project for candidates matching property filters.

    All filters AND together. Returns compact candidate rows (material_id,
    formula, key properties). Use get_material for the full sheet.
    """
    filters = SearchFilters(
        elements=elements,
        exclude_elements=exclude_elements,
        formula=formula,
        band_gap_range=band_gap_range,
        density_range=density_range,
        max_energy_above_hull=max_energy_above_hull,
        only_stable=only_stable,
        num_elements=num_elements,
        is_metal=is_metal,
        is_gap_direct=is_gap_direct,
        limit=limit,
    )
    return [c.model_dump(mode="json") for c in _search_materials(filters)]


def get_material(material_id: str) -> dict[str, Any]:
    """Return the full property sheet for one Materials Project id (e.g. 'mp-149')."""
    return _get_material(material_id).model_dump(mode="json")


def compare_materials(
    material_ids: list[str],
    properties: list[str] | None = None,
) -> dict[str, Any]:
    """Side-by-side comparison of multiple materials on selected properties.

    If ``properties`` is omitted, uses a default set
    (band_gap, density, energy_above_hull, formation_energy_per_atom,
    is_metal, is_stable). Supports dotted paths like 'symmetry.crystal_system'.
    """
    return _compare_materials(material_ids, properties).model_dump(mode="json")


def check_stability(material_id: str) -> dict[str, Any]:
    """Stability verdict + human-readable explanation for one material.

    Returns: material_id, formula, energy_above_hull, formation_energy_per_atom,
    is_stable, verdict ('stable' | 'metastable' | 'unstable'), explanation.
    """
    return _check_stability(material_id).model_dump(mode="json")


def get_phase_diagram(
    chemsys: str,
    max_energy_above_hull: float = 0.2,
    limit: int = 80,
) -> dict[str, Any]:
    """All phases in a chemical system, sorted by distance from the convex hull.

    Use this to answer 'what competing materials exist in the X-Y-Z system?'
    Returns stable_phases (on the hull) and metastable_phases (above it,
    sorted by ascending energy_above_hull). Order of elements in chemsys
    doesn't matter ('Li-Fe-O' == 'O-Fe-Li').

    Args:
        chemsys: dash-separated chemical system, e.g. 'Li-Fe-O' or 'Si-O'.
        max_energy_above_hull: cap on E above hull (eV/atom). Default 0.2.
        limit: max phases to return. Default 80.
    """
    return _get_phase_diagram(chemsys, max_energy_above_hull=max_energy_above_hull, limit=limit)


def predict_decomposition(material_id: str) -> dict[str, Any]:
    """For a given material, list the stable competing phases that bound its
    equilibrium decomposition (in the same chemsys).

    Use this to answer 'if I tried to make this material in the lab, what
    else would form?' Returns verdict (stable/metastable/unstable), the
    on-hull phases in the same chemsys, and a human-readable interpretation
    of synthesizability.
    """
    return _predict_decomposition(material_id)


def get_competing_phases(
    formula: str,
    max_energy_above_hull: float = 0.05,
    limit: int = 40,
) -> dict[str, Any]:
    """All phases in the chemsys of `formula`, regardless of stoichiometry.

    Use this to ask 'what other compositions could form in the same
    element set?'. Returns stable_phases (on the hull) and metastable
    neighbors. anchor_formula in the response echoes the input.

    Args:
        formula: pretty formula like 'Fe2O3' or 'LiFePO4'.
        max_energy_above_hull: cap on E above hull (eV/atom). Default 0.05.
        limit: max phases to return. Default 40.
    """
    return _get_competing_phases(formula, max_energy_above_hull=max_energy_above_hull, limit=limit)


# Canonical registry — both MCP server and agent runner iterate over this list.
ALL_TOOLS: list[Callable[..., Any]] = [
    search_materials,
    get_material,
    compare_materials,
    check_stability,
    get_phase_diagram,
    predict_decomposition,
    get_competing_phases,
]
