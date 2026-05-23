"""get_material — full property sheet for a single MP id, with cache."""

from __future__ import annotations

from typing import Any

from matscout.models import Material, Symmetry
from matscout.tools._client import get_cache, get_client

# Full sheet — superset of what search_materials pulls. Avoid heavy fields
# like raw `structure` and `dos` here; the agent can ask for them by name
# in a follow-up if it ever needs them.
_FULL_FIELDS = [
    "material_id",
    "formula_pretty",
    "formula_anonymous",
    "chemsys",
    "elements",
    "nelements",
    "nsites",
    "volume",
    "density",
    "density_atomic",
    "symmetry",
    "band_gap",
    "is_gap_direct",
    "is_metal",
    "is_magnetic",
    "energy_above_hull",
    "formation_energy_per_atom",
    "uncorrected_energy_per_atom",
    "is_stable",
    "bulk_modulus",
    "shear_modulus",
    "n",
    "total_magnetization",
    "theoretical",
    "deprecated",
]


class MaterialNotFoundError(LookupError):
    """Raised when MP returns no document for the requested id."""


def _doc_to_material(doc: Any) -> Material:
    elements_raw = getattr(doc, "elements", None) or []
    elements: list[str] = [
        getattr(e, "symbol", None) or getattr(e, "name", None) or str(e) for e in elements_raw
    ]

    sym_obj = getattr(doc, "symmetry", None)
    symmetry: Symmetry | None = None
    if sym_obj is not None:
        cs = getattr(sym_obj, "crystal_system", None)
        cs_str = getattr(cs, "value", None) if cs is not None else None
        cs_str = (cs_str or str(cs or "Unknown")).title()
        try:
            symmetry = Symmetry(
                crystal_system=cs_str,
                spacegroup_symbol=getattr(sym_obj, "symbol", None),
                spacegroup_number=getattr(sym_obj, "number", None),
                point_group=getattr(sym_obj, "point_group", None),
            )
        except Exception:
            symmetry = None

    # MP returns bulk_modulus / shear_modulus either as a dict
    # {voigt, reuss, vrh} or sometimes a pymatgen object — normalize to dict.
    def _modulus(val: Any) -> dict[str, float] | None:
        if val is None:
            return None
        if isinstance(val, dict):
            return {k: float(v) for k, v in val.items() if v is not None}
        out: dict[str, float] = {}
        for k in ("voigt", "reuss", "vrh"):
            v = getattr(val, k, None)
            if v is not None:
                out[k] = float(v)
        return out or None

    return Material(
        material_id=str(getattr(doc, "material_id", "")),
        formula_pretty=str(getattr(doc, "formula_pretty", "")),
        formula_anonymous=getattr(doc, "formula_anonymous", None),
        chemsys=getattr(doc, "chemsys", None),
        elements=elements,
        nelements=int(getattr(doc, "nelements", len(elements) or 1)),
        nsites=getattr(doc, "nsites", None),
        volume=getattr(doc, "volume", None),
        density=getattr(doc, "density", None),
        density_atomic=getattr(doc, "density_atomic", None),
        symmetry=symmetry,
        band_gap=getattr(doc, "band_gap", None),
        is_gap_direct=getattr(doc, "is_gap_direct", None),
        is_metal=getattr(doc, "is_metal", None),
        is_magnetic=getattr(doc, "is_magnetic", None),
        energy_above_hull=getattr(doc, "energy_above_hull", None),
        formation_energy_per_atom=getattr(doc, "formation_energy_per_atom", None),
        uncorrected_energy_per_atom=getattr(doc, "uncorrected_energy_per_atom", None),
        is_stable=getattr(doc, "is_stable", None),
        bulk_modulus=_modulus(getattr(doc, "bulk_modulus", None)),
        shear_modulus=_modulus(getattr(doc, "shear_modulus", None)),
        refractive_index=getattr(doc, "n", None),
        total_magnetization=getattr(doc, "total_magnetization", None),
        theoretical=bool(getattr(doc, "theoretical", False)),
        deprecated=bool(getattr(doc, "deprecated", False)),
    )


def get_material(material_id: str) -> Material:
    """Return the full property sheet for ``material_id`` (e.g. ``'mp-149'``).

    Raises :class:`MaterialNotFoundError` if MP has no record under that id.
    """
    if not material_id or not material_id.strip():
        raise ValueError("material_id must be a non-empty string")

    cache_args = {"material_id": material_id}
    cache = get_cache()
    cached = cache.get("get_material", cache_args)
    if cached is not None:
        return Material.model_validate(cached)

    client = get_client()
    docs = client.materials.summary.search(
        material_ids=[material_id],
        fields=_FULL_FIELDS,
    )
    if not docs:
        raise MaterialNotFoundError(f"No material with id={material_id!r} in Materials Project")

    material = _doc_to_material(docs[0])
    cache.put("get_material", cache_args, material.model_dump(mode="json"))
    return material
