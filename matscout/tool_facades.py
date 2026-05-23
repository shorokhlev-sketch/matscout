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

from typing import Any

from matscout.models import SearchFilters
from matscout.tools import (
    check_stability as _check_stability,
)
from matscout.tools import (
    compare_materials as _compare_materials,
)
from matscout.tools import (
    get_material as _get_material,
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


# Canonical registry — both MCP server and agent runner iterate over this list.
ALL_TOOLS = [search_materials, get_material, compare_materials, check_stability]
