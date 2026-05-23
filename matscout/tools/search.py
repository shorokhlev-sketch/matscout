"""search_materials — typed filter → list[Candidate], with cache.

This is the only place that translates our SearchFilters dialect into
mp-api kwargs. mcp_server and the agent both call the same function;
they don't see SummaryDoc at all.
"""

from __future__ import annotations

from typing import Any

from matscout.models import Candidate, SearchFilters, Symmetry
from matscout.tools._client import get_cache, get_client

# Compact list of fields requested from MP — we keep this lean to avoid
# pulling huge structures into list responses. Full sheet is in get_material.
_LIST_FIELDS = [
    "material_id",
    "formula_pretty",
    "elements",
    "nelements",
    "band_gap",
    "density",
    "energy_above_hull",
    "formation_energy_per_atom",
    "is_stable",
    "is_metal",
    "symmetry",
]


def _filters_to_mp_kwargs(f: SearchFilters) -> dict[str, Any]:
    """SearchFilters → kwargs accepted by mpr.materials.summary.search()."""
    kw: dict[str, Any] = {"fields": _LIST_FIELDS}
    if f.elements:
        kw["elements"] = f.elements
    if f.exclude_elements:
        kw["exclude_elements"] = f.exclude_elements
    if f.formula:
        kw["formula"] = f.formula
    if f.band_gap_range:
        kw["band_gap"] = f.band_gap_range
    if f.density_range:
        kw["density"] = f.density_range
    if f.max_energy_above_hull is not None:
        kw["energy_above_hull"] = (0.0, f.max_energy_above_hull)
    if f.only_stable:
        # The MP API has a direct `is_stable` filter — prefer it over
        # tweaking energy_above_hull bounds ourselves.
        kw["is_stable"] = True
    if f.num_elements is not None:
        kw["num_elements"] = f.num_elements
    if f.is_metal is not None:
        kw["is_metal"] = f.is_metal
    if f.is_gap_direct is not None:
        kw["is_gap_direct"] = f.is_gap_direct
    return kw


def _doc_to_candidate(doc: Any) -> Candidate:
    """Map a SummaryDoc instance to our Candidate model.

    pymatgen Element objects → str symbols.
    SymmetryData → our normalized Symmetry.
    """
    elements_raw = getattr(doc, "elements", None) or []
    elements: list[str] = [
        getattr(e, "symbol", None) or getattr(e, "name", None) or str(e) for e in elements_raw
    ]

    sym_obj = getattr(doc, "symmetry", None)
    symmetry: Symmetry | None = None
    if sym_obj is not None:
        cs = getattr(sym_obj, "crystal_system", None)
        # pymatgen CrystalSystem enum has .value; fall back to str() then title()
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
            # If MP returns something we can't normalize cleanly, drop symmetry
            # rather than fail the whole row.
            symmetry = None

    return Candidate(
        material_id=str(getattr(doc, "material_id", "")),
        formula_pretty=str(getattr(doc, "formula_pretty", "")),
        elements=elements,
        nelements=int(getattr(doc, "nelements", len(elements) or 1)),
        band_gap=getattr(doc, "band_gap", None),
        density=getattr(doc, "density", None),
        energy_above_hull=getattr(doc, "energy_above_hull", None),
        formation_energy_per_atom=getattr(doc, "formation_energy_per_atom", None),
        is_stable=getattr(doc, "is_stable", None),
        is_metal=getattr(doc, "is_metal", None),
        symmetry=symmetry,
    )


def search_materials(filters: SearchFilters | None = None, **kwargs: Any) -> list[Candidate]:
    """Search the Materials Project. Returns up to `filters.limit` candidates.

    Either pass a fully-built ``SearchFilters`` or kwargs that match its fields
    (the agent and MCP-tool layer use kwargs). Cache hits skip the API entirely.
    """
    f = filters if filters is not None else SearchFilters(**kwargs)
    cache_args = f.model_dump(mode="json", exclude_none=True)

    cache = get_cache()
    cached = cache.get("search_materials", cache_args)
    if cached is not None:
        return [Candidate.model_validate(row) for row in cached["candidates"]]

    client = get_client()
    mp_kwargs = _filters_to_mp_kwargs(f)
    docs = client.materials.summary.search(**mp_kwargs)
    candidates = [_doc_to_candidate(d) for d in docs[: f.limit]]

    cache.put(
        "search_materials",
        cache_args,
        {"candidates": [c.model_dump(mode="json") for c in candidates]},
    )
    return candidates
