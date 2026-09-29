"""compare_materials - side-by-side property table for N materials.

Built on top of get_material - no separate API calls, no separate cache.
The agent can ask for arbitrary properties; we resolve them by attribute
access on the Material model, with a sensible default set when omitted.
"""

from __future__ import annotations

from typing import Any

from matscout.models import ComparisonRow, ComparisonTable, Material
from matscout.tools.get import get_material

# Properties shown when the caller doesn't specify any - the dimensions a
# materials engineer almost always wants to compare.
_DEFAULT_PROPERTIES = [
    "band_gap",
    "density",
    "energy_above_hull",
    "formation_energy_per_atom",
    "is_metal",
    "is_stable",
]


def _resolve_property(material: Material, prop: str) -> Any:
    """Extract a property value from a Material, walking dotted paths if needed."""
    obj: Any = material
    for part in prop.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def compare_materials(
    material_ids: list[str],
    properties: list[str] | None = None,
) -> ComparisonTable:
    """Build a row-per-material x column-per-property table for UI rendering.

    Properties default to a sensible set (band_gap, density, e_above_hull,
    formation_energy_per_atom, is_metal, is_stable). Dotted paths work for
    nested fields, e.g. ``"symmetry.crystal_system"``.
    """
    if not material_ids:
        raise ValueError("material_ids must contain at least one id")
    props = list(properties) if properties else list(_DEFAULT_PROPERTIES)

    rows: list[ComparisonRow] = []
    for mid in material_ids:
        m = get_material(mid)
        values: dict[str, Any] = {}
        for p in props:
            v = _resolve_property(m, p)
            # Coerce non-JSON-friendly objects (Enum, Path, etc.) to printable
            if hasattr(v, "value"):
                v = v.value
            values[p] = v
        rows.append(
            ComparisonRow(material_id=m.material_id, formula_pretty=m.formula_pretty, values=values)
        )

    return ComparisonTable(properties=props, rows=rows)
