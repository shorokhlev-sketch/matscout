"""FastMCP-server exposing the 4 tools to any MCP client (Claude Code etc.).

Wrappers are intentionally thin: they translate flat kwargs (which is how
MCP/JSON-schema speaks) into our Pydantic input model and convert
Pydantic outputs back to dicts. The actual logic lives in ``matscout.tools``.

Run:
    matscout-mcp                # console_script installed by pyproject
    python -m matscout.mcp_server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

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

mcp = FastMCP(
    "matscout",
    instructions=(
        "Materials Project search agent. Use these tools to find inorganic "
        "crystalline materials matching property constraints. Workflow: "
        "1) search_materials with reasonable filters → 2) inspect top "
        "candidates with get_material / check_stability → 3) compare_materials "
        "side-by-side. Energies are in eV/atom, band gaps in eV, density in "
        "g/cm^3. Materials with energy_above_hull > 0.025 eV/atom are "
        "unlikely to be synthesizable as a single phase."
    ),
)


@mcp.tool()
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

    Args:
        elements: required elements, e.g. ["Si", "O"].
        exclude_elements: elements that must NOT appear in the material.
        formula: pretty formula "Fe2O3" or anonymous template "ABO3", "Si*".
        band_gap_range: (min, max) band gap in eV.
        density_range: (min, max) density in g/cm^3.
        max_energy_above_hull: upper bound on E above hull in eV/atom
            (0.025 is the conventional metastable cutoff).
        only_stable: restrict to phases on the convex hull (e_above_hull == 0).
        num_elements: exact number of distinct elements (1=elemental, 2=binary, ...).
        is_metal: True → only metals; False → only non-metals.
        is_gap_direct: if True, only materials with a direct band gap.
        limit: max candidates to return (default 50, max 500).
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


@mcp.tool()
def get_material(material_id: str) -> dict[str, Any]:
    """Return the full property sheet for one Materials Project id.

    Args:
        material_id: MP id like 'mp-149' (Si), 'mp-66' (diamond).

    Returns a dict with structural, electronic, mechanical, magnetic, and
    formation-energy fields when available. None for missing data.
    """
    return _get_material(material_id).model_dump(mode="json")


@mcp.tool()
def compare_materials(
    material_ids: list[str],
    properties: list[str] | None = None,
) -> dict[str, Any]:
    """Side-by-side comparison of multiple materials on selected properties.

    Args:
        material_ids: list of MP ids to compare.
        properties: which properties to include. If omitted, uses a default
            set (band_gap, density, energy_above_hull, formation_energy_per_atom,
            is_metal, is_stable). Supports dotted paths like
            'symmetry.crystal_system'.
    """
    return _compare_materials(material_ids, properties).model_dump(mode="json")


@mcp.tool()
def check_stability(material_id: str) -> dict[str, Any]:
    """Stability verdict + human-readable explanation for one material.

    Returns:
        material_id, formula, energy_above_hull, formation_energy_per_atom,
        is_stable, verdict ('stable' | 'metastable' | 'unstable'), and a
        one-line explanation suitable for showing the user.
    """
    return _check_stability(material_id).model_dump(mode="json")


def main() -> None:
    """Console entrypoint — runs FastMCP over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
