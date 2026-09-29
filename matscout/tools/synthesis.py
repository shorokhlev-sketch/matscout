"""Synthesis-aware tools - phase diagrams, decomposition, competing phases.

These three sit on top of the same MPRester + cache as the rest of the
toolset. They turn the agent from a "find me a stable material" assistant
into a "find me a stable material AND tell me what I'd actually have to
fight in the lab" assistant.

Naming convention:
    get_phase_diagram(chemsys)        - every entry in the chemsys + hull marks
    predict_decomposition(mp_id)      - for off-hull phases, what does it decay into?
    get_competing_phases(formula)     - alternative stoichiometries in the same chemsys
"""

from __future__ import annotations

from typing import Any

from matscout.models import Candidate, Symmetry
from matscout.tools._client import get_cache, get_client
from matscout.tools.get import get_material

# Fields requested for phase-diagram-style queries - same compact set as
# search_materials so output stays digestible for the LLM.
_PHASE_FIELDS = [
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


def _normalize_chemsys(chemsys: str) -> str:
    """Canonicalise a chemsys string so cache keys collide on equivalent inputs.

    'Li-Fe-O' and 'O-Li-Fe' should be the same key. The MP API itself accepts
    either order, but we want our cache to be smart about it.
    """
    parts = [p.strip().title() for p in chemsys.replace(",", "-").split("-") if p.strip()]
    if not parts:
        raise ValueError(f"invalid chemsys: {chemsys!r}")
    return "-".join(sorted(parts))


def _doc_to_candidate(doc: Any) -> Candidate:
    """Same mapping as in search.py - duplicated here to avoid an import cycle."""
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


def get_phase_diagram(
    chemsys: str,
    *,
    max_energy_above_hull: float = 0.2,
    limit: int = 80,
) -> dict[str, Any]:
    """All phases in a chemsys, sorted by ``energy_above_hull``.

    Args:
        chemsys: dash-separated element list, e.g. ``"Li-Fe-O"`` or
            ``"Si-O"``. Order doesn't matter; we canonicalize.
        max_energy_above_hull: cap (eV/atom). Defaults to 200 meV/atom -
            broad enough to see the metastable neighborhood, narrow
            enough to keep the response readable.
        limit: max phases to return.

    Returns:
        ``{"chemsys": ..., "n_phases": ..., "n_stable": ...,
           "stable_phases": [...], "metastable_phases": [...]}``

        ``stable_phases`` are on the convex hull (e_above_hull <= 1e-9).
        ``metastable_phases`` are everything below the threshold but
        above the hull, sorted by distance from the hull.
    """
    canon = _normalize_chemsys(chemsys)
    cache_args = {
        "chemsys": canon,
        "max_eah": max_energy_above_hull,
        "limit": limit,
    }
    cache = get_cache()
    cached = cache.get("get_phase_diagram", cache_args)
    if cached is not None:
        return _hydrate_phase_diagram(cached)

    client = get_client()
    docs = list(
        client.materials.summary.search(
            chemsys=canon,
            energy_above_hull=(0.0, max_energy_above_hull),
            fields=_PHASE_FIELDS,
        )
    )

    # A phase diagram without its elemental endpoints is geometrically
    # degenerate - there's nothing for the convex hull to anchor on.
    # MP rarely surfaces them in a multi-element chemsys query (a search
    # over chemsys="Li-O" filters to compounds, not pure Li or pure O),
    # so we fetch the stable elemental references explicitly using
    # ``elements=[X], nelements=1`` - the MP-idiomatic way to ask for
    # the pure-element entries - and keep just the lowest-EAH one per
    # element. Cheap (one MP call per element, both cached).
    elements_in_chemsys = canon.split("-")
    if len(elements_in_chemsys) >= 2:
        seen_mp_ids = {str(getattr(d, "material_id", "")) for d in docs}
        for el in elements_in_chemsys:
            # `num_elements` is the post-2024 MP parameter; `nelements` is the
            # deprecated alias that MPRester silently ignores (just emits a
            # warning and returns the unfiltered set). Use the new name.
            el_docs = list(
                client.materials.summary.search(
                    chemsys=el,  # single-element chemsys == pure-element entries
                    fields=_PHASE_FIELDS,
                )
            )
            # Pick the on-hull (or closest-to-hull) elemental entry.
            el_docs.sort(key=lambda d: (getattr(d, "energy_above_hull", None) or 1e9,))
            for ed in el_docs[:1]:
                mid = str(getattr(ed, "material_id", ""))
                if mid and mid not in seen_mp_ids:
                    docs.append(ed)
                    seen_mp_ids.add(mid)

    candidates = sorted(
        (_doc_to_candidate(d) for d in docs),
        key=lambda c: c.energy_above_hull if c.energy_above_hull is not None else 1e9,
    )[:limit]

    stable = [c for c in candidates if (c.energy_above_hull or 0) <= 1e-9]
    metastable = [c for c in candidates if (c.energy_above_hull or 0) > 1e-9]

    result = {
        "chemsys": canon,
        "max_energy_above_hull": max_energy_above_hull,
        "n_phases": len(candidates),
        "n_stable": len(stable),
        "n_metastable": len(metastable),
        "stable_phases": [c.model_dump(mode="json") for c in stable],
        "metastable_phases": [c.model_dump(mode="json") for c in metastable],
    }
    cache.put("get_phase_diagram", cache_args, result)
    return result


def _hydrate_phase_diagram(raw: dict[str, Any]) -> dict[str, Any]:
    """Round-trip cached JSON through Candidate validation, then back to JSON."""
    out = dict(raw)
    for key in ("stable_phases", "metastable_phases"):
        out[key] = [Candidate.model_validate(c).model_dump(mode="json") for c in out.get(key, [])]
    return out


def predict_decomposition(material_id: str) -> dict[str, Any]:
    """For an off-hull phase, list the stable competing phases in the same chemsys.

    True equilibrium decomposition products require a full pymatgen
    PhaseDiagram object (we'd have to fetch every entry in the chemsys
    and run convex-hull algebra). That's heavy for what we can offer
    without a pymatgen-grade compute backend. The honest substitute:
    return the *stable phases in the same chemsys* - these are exactly
    the products such a decomposition would have to land on. The agent
    can reason about ratios from there.

    Args:
        material_id: MP id, e.g. ``"mp-1234"``.

    Returns:
        ``{
            "material_id": "...",
            "formula_pretty": "...",
            "chemsys": "Li-Fe-O",
            "energy_above_hull": 0.05,
            "verdict": "stable" | "metastable" | "unstable",
            "on_hull": bool,
            "competing_stable_phases": [Candidate],
            "interpretation": str
        }``
    """
    material = get_material(material_id)
    e = material.energy_above_hull
    chemsys = material.chemsys or "-".join(sorted(material.elements))

    pd = get_phase_diagram(chemsys, max_energy_above_hull=0.001, limit=50)
    competing = pd["stable_phases"]

    if e is None:
        interpretation = (
            "No stability data available for this material; cannot predict decomposition. "
            "The stable phases listed are the products any decomposition would have to land on."
        )
        verdict = "unstable"
    elif e <= 1e-9:
        interpretation = (
            "This phase IS on the convex hull; it does not decompose under "
            "equilibrium conditions. The 'competing_stable_phases' here are just "
            "the rest of the hull (including this entry itself)."
        )
        verdict = "stable"
    elif e <= 0.025:
        interpretation = (
            f"Metastable, {e * 1000:.0f} meV/atom above the hull. Likely persists "
            f"under typical synthesis conditions but, given enough thermal energy "
            f"or time, would decompose into the listed competing stable phases."
        )
        verdict = "metastable"
    else:
        interpretation = (
            f"Unstable: {e * 1000:.0f} meV/atom above the hull. Strongly favors "
            f"decomposition into the competing stable phases listed. Single-phase "
            f"synthesis of this composition is unlikely without kinetic stabilization."
        )
        verdict = "unstable"

    return {
        "material_id": material.material_id,
        "formula_pretty": material.formula_pretty,
        "chemsys": chemsys,
        "energy_above_hull": e,
        "formation_energy_per_atom": material.formation_energy_per_atom,
        "verdict": verdict,
        "on_hull": bool(e is not None and e <= 1e-9),
        "competing_stable_phases": competing,
        "interpretation": interpretation,
    }


def get_competing_phases(
    formula: str,
    *,
    max_energy_above_hull: float = 0.05,
    limit: int = 40,
) -> dict[str, Any]:
    """All phases in the chemsys of ``formula``, regardless of stoichiometry.

    The intent: 'I want compositions adjacent to Fe2O3' → return every
    Fe-O phase MP knows about. Use case in the lab: scoping which
    side-products to expect or screen for.

    Args:
        formula: pretty formula, e.g. ``"Fe2O3"`` or ``"LiFePO4"``.
        max_energy_above_hull: cap (eV/atom) - same semantics as in
            ``get_phase_diagram``.
        limit: max phases to return.
    """
    # Cache key independent of MP query - chemsys is derived from formula.
    cache_args = {
        "formula": formula,
        "max_eah": max_energy_above_hull,
        "limit": limit,
    }
    cache = get_cache()
    cached = cache.get("get_competing_phases", cache_args)
    if cached is not None:
        return cached  # already JSON-safe

    # We need a chemsys to query - easiest path is one MP roundtrip for the
    # base formula to learn its element set.
    client = get_client()
    seed = client.materials.summary.search(
        formula=formula,
        fields=["material_id", "elements", "chemsys"],
        num_chunks=1,
        chunk_size=1,
    )
    if not seed:
        raise ValueError(f"Materials Project knows no entry with formula={formula!r}")
    chemsys = getattr(seed[0], "chemsys", None) or "-".join(
        sorted(getattr(e, "symbol", str(e)) for e in (getattr(seed[0], "elements", None) or []))
    )

    pd = get_phase_diagram(chemsys, max_energy_above_hull=max_energy_above_hull, limit=limit)
    result = {
        "anchor_formula": formula,
        "chemsys": pd["chemsys"],
        "n_phases": pd["n_phases"],
        "n_stable": pd["n_stable"],
        "n_metastable": pd["n_metastable"],
        "stable_phases": pd["stable_phases"],
        "metastable_phases": pd["metastable_phases"],
    }
    cache.put("get_competing_phases", cache_args, result)
    return result
