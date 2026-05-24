"""Strict phase-diagram analytics via pymatgen.

Where ``get_phase_diagram`` just returns MP's tabulated entries, this
module runs the actual ``PhaseDiagram`` convex-hull math: for an
off-hull entry, it tells you what stable phases it would decompose
into, with the molar coefficients, plus the energy released by the
decomposition reaction. This is what a synthesis chemist actually
wants when they ask "will this thing make in the lab?".
"""

from __future__ import annotations

from typing import Any

from matscout.tools._client import get_cache, get_client
from matscout.tools.synthesis import _normalize_chemsys


def compute_phase_diagram_strict(
    chemsys: str,
    *,
    include_decomposition: bool = True,
    limit_offhull_examples: int = 5,
) -> dict[str, Any]:
    """Run pymatgen's PhaseDiagram math on every MP entry in a chemsys.

    Args:
        chemsys: dash-separated elements like ``"Li-Fe-O"``.
        include_decomposition: if True, also compute decomposition
            products + reaction energy for the top-``limit_offhull_examples``
            metastable entries. Adds one extra pymatgen call per off-hull
            entry, so disable when you only need the hull itself.
        limit_offhull_examples: how many off-hull entries to enrich with
            decomposition details (most-stable-first).

    Returns:
        ``{
            "chemsys": str,
            "n_entries_total": int,
            "n_stable": int,
            "stable": [{"material_id", "formula", "formation_energy_per_atom"}],
            "decompositions": [
                {
                    "material_id", "formula", "e_above_hull",
                    "decomposes_to": [
                        {"material_id", "formula", "molar_fraction"}
                    ],
                    "reaction_str": "Li2O + Fe2O3 → LiFeO2"  (rough)
                }
            ],
            "interpretation": str,
        }``
    """
    canon = _normalize_chemsys(chemsys)
    cache_args = {
        "chemsys": canon,
        "decomp": include_decomposition,
        "offhull": limit_offhull_examples,
    }
    cache = get_cache()
    cached = cache.get("compute_phase_diagram_strict", cache_args)
    if cached is not None:
        return cached

    # pymatgen is heavy; import only when this tool is actually called.
    from pymatgen.analysis.phase_diagram import PhaseDiagram

    client = get_client()
    elements = canon.split("-")
    entries = client.get_entries_in_chemsys(elements)  # type: ignore[attr-defined]
    if not entries:
        result: dict[str, Any] = {
            "chemsys": canon,
            "n_entries_total": 0,
            "n_stable": 0,
            "stable": [],
            "decompositions": [],
            "interpretation": "Materials Project has no entries in this chemsys.",
        }
        cache.put("compute_phase_diagram_strict", cache_args, result)
        return result

    pd = PhaseDiagram(entries)
    stable_ids = {getattr(e, "entry_id", None) or "" for e in pd.stable_entries}

    stable_rows: list[dict[str, Any]] = []
    for entry in pd.stable_entries:
        comp = entry.composition
        stable_rows.append(
            {
                "material_id": getattr(entry, "entry_id", None) or "",
                "formula": comp.reduced_formula,
                "formation_energy_per_atom": round(
                    float(pd.get_form_energy_per_atom(entry)),  # type: ignore[arg-type]
                    4,
                ),
            }
        )
    stable_rows.sort(key=lambda r: r["formation_energy_per_atom"])

    decompositions: list[dict[str, Any]] = []
    if include_decomposition:
        # Sort off-hull entries by E_above_hull (ascending) so the
        # examples shown are the most synthesisable-ish first.
        off_hull: list[tuple[Any, float]] = []
        for e in entries:
            eid = getattr(e, "entry_id", None) or ""
            if eid in stable_ids:
                continue
            try:
                eah_val = float(pd.get_e_above_hull(e) or 0.0)
            except Exception:
                continue
            off_hull.append((e, eah_val))
        off_hull.sort(key=lambda pair: pair[1])
        for entry, eah in off_hull[:limit_offhull_examples]:
            decomp = pd.get_decomposition(entry.composition)
            products = []
            for prod_entry, coef in decomp.items():
                products.append(
                    {
                        "material_id": getattr(prod_entry, "entry_id", None) or "",
                        "formula": prod_entry.composition.reduced_formula,
                        "molar_fraction": round(float(coef), 4),
                    }
                )
            reaction_str = " + ".join(
                f"{round(float(p['molar_fraction']), 2)} {p['formula']}" for p in products
            )
            decompositions.append(
                {
                    "material_id": getattr(entry, "entry_id", None) or "",
                    "formula": entry.composition.reduced_formula,
                    "e_above_hull": round(eah, 4),
                    "decomposes_to": products,
                    "reaction_str": (f"{entry.composition.reduced_formula} → {reaction_str}"),
                }
            )

    interpretation = (
        f"PhaseDiagram built from {len(entries)} MP entries; "
        f"{len(stable_rows)} sit on the convex hull, "
        f"{len(entries) - len(stable_rows)} are off-hull. "
        + (
            f"Showing decomposition reactions for the {len(decompositions)} closest-to-hull "
            f"metastable phases — these are the products their synthesis would compete with."
            if decompositions
            else ""
        )
    )

    result = {
        "chemsys": canon,
        "n_entries_total": len(entries),
        "n_stable": len(stable_rows),
        "stable": stable_rows,
        "decompositions": decompositions,
        "interpretation": interpretation,
    }
    cache.put("compute_phase_diagram_strict", cache_args, result)
    return result
