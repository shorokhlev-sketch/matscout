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
    compute_phase_diagram_strict as _compute_phase_diagram_strict,
)
from matscout.tools import (
    find_2d_materials as _find_2d_materials,
)
from matscout.tools import (
    find_battery_anode as _find_battery_anode,
)
from matscout.tools import (
    find_battery_cathode as _find_battery_cathode,
)
from matscout.tools import (
    find_papers as _find_papers,
)
from matscout.tools import (
    find_preprints as _find_preprints,
)
from matscout.tools import (
    find_solar_absorber as _find_solar_absorber,
)
from matscout.tools import (
    find_thermoelectric as _find_thermoelectric,
)
from matscout.tools import (
    find_transparent_conductor as _find_transparent_conductor,
)
from matscout.tools import (
    get_competing_phases as _get_competing_phases,
)
from matscout.tools import (
    get_doi_metadata as _get_doi_metadata,
)
from matscout.tools import (
    get_elastic_properties as _get_elastic_properties,
)
from matscout.tools import (
    get_electronic_summary as _get_electronic_summary,
)
from matscout.tools import (
    get_jarvis_topological as _get_jarvis_topological,
)
from matscout.tools import (
    get_material as _get_material,
)
from matscout.tools import (
    get_papers_about as _get_papers_about,
)
from matscout.tools import (
    get_phase_diagram as _get_phase_diagram,
)
from matscout.tools import (
    get_structure as _get_structure,
)
from matscout.tools import (
    pareto_rank as _pareto_rank,
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
) -> dict[str, Any]:
    """Search Materials Project for candidates matching property filters.

    All filters AND together. Returns ``{"count": N, "materials": [...]}``
    with compact candidate rows (material_id, formula, key properties).
    Use get_material for the full sheet of any individual candidate.
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
    cands = [c.model_dump(mode="json") for c in _search_materials(filters)]
    return {"count": len(cands), "materials": cands}


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


def find_papers(
    query: str,
    year_from: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search Semantic Scholar for academic papers matching `query`.

    Use this for 'literature review' style asks. Returns up to `limit`
    papers with title, authors, year, venue, DOI, abstract snippet, and
    citation count. Bias the agent to call this after finding candidates
    in MP — readers want context, not just numbers.

    Args:
        query: free-text query, e.g. 'cathode materials Na-ion battery'.
        year_from: minimum publication year (inclusive). None = no bound.
        limit: max papers returned. Default 10, cap 100.
    """
    return _find_papers(query, year_from=year_from, limit=limit).model_dump(mode="json")


def get_papers_about(
    material_id_or_formula: str,
    year_from: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find recent papers that mention a specific material (by mp-id or formula).

    Use this when the user wants 'what's been published about X recently'.
    Internally biases the search toward materials-science context so a
    raw formula doesn't accidentally hit unrelated literature.
    """
    return _get_papers_about(material_id_or_formula, year_from=year_from, limit=limit).model_dump(
        mode="json"
    )


def get_doi_metadata(doi: str) -> dict[str, Any]:
    """Resolve a DOI to a canonical bibrecord (CrossRef).

    Use this to expand a DOI string from the user or from another tool's
    result into authors / year / journal / abstract.
    """
    return _get_doi_metadata(doi).model_dump(mode="json")


def get_structure(
    material_id: str,
    fmt: str = "cif",
) -> dict[str, Any]:
    """Return the canonical crystal structure as a text blob ready for DFT input.

    Use this when the user wants to download/use the structure file in their
    own calculation. `fmt`:
      - 'cif'    → CIF (Quantum ESPRESSO, GPAW, OVITO, VESTA — recommended default)
      - 'poscar' → POSCAR (VASP)
      - 'xyz'    → XYZ (visualization only, periodicity is lost)

    Returns the content plus suggested filename and structural metadata
    (lattice, spacegroup, n_sites).
    """
    return _get_structure(material_id, fmt=fmt)  # type: ignore[arg-type]


def find_preprints(
    query: str,
    max_age_days: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search arXiv for preprints matching `query`, sorted by submission date.

    Use this when the user wants bleeding-edge results that may not be in
    a journal yet. `max_age_days` lets you filter (coarse-grained, year level).
    Returns up to `limit` preprints with title, authors, year, PDF URL.
    """
    return _find_preprints(query, max_age_days=max_age_days, limit=limit).model_dump(mode="json")


# ── Application-aware combinators ──────────────────────────────────────────


def _wrap_list(cands: list[Any]) -> dict[str, Any]:
    """Return a single-dict wrapper so FastMCP serialises one content block.

    FastMCP serialises ``list[dict]`` returns as N separate text-content
    blocks, and OpenAI's MCP-tool integration only carries the first
    block into the model's view. Wrapping the list keeps all candidates
    visible to the agent.
    """
    rows = [c.model_dump(mode="json") for c in cands]
    return {"count": len(rows), "materials": rows}


def find_battery_anode(
    chemistry: str = "lithium",
    limit: int = 10,
) -> dict[str, Any]:
    """Pre-tuned anode candidates for solid-state batteries.

    Use this instead of generic search_materials whenever the user asks
    for "anode", "battery negative electrode", or names a specific
    chemistry. Filters by the element shortlist that real Li / Na / Mg
    / K anodes use, excludes radioactives, and skips pure metals that
    aren't intercalation hosts. Returns
    ``{"count": N, "materials": [...]}``.

    Args:
        chemistry: "lithium" | "sodium" | "magnesium" | "potassium".
        limit: how many candidates to return.
    """
    return _wrap_list(_find_battery_anode(chemistry=chemistry, limit=limit))  # type: ignore[arg-type]


def find_battery_cathode(
    chemistry: str = "lithium",
    limit: int = 10,
) -> dict[str, Any]:
    """Pre-tuned cathode candidates (intercalation oxides / phosphates).

    Filters to mixed-valence transition-metal compounds containing the
    working ion (Li or Na). Use for "cathode", "positive electrode",
    "LiCoO2-class", etc. Returns ``{"count": N, "materials": [...]}``.

    Args:
        chemistry: "lithium" | "sodium".
        limit: how many candidates to return.
    """
    return _wrap_list(_find_battery_cathode(chemistry=chemistry, limit=limit))  # type: ignore[arg-type]


def find_solar_absorber(
    exclude_toxic: bool = True,
    require_direct_gap: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Single-junction solar absorbers near the Shockley-Queisser optimum.

    Band gap 1.1-1.7 eV, stable, optionally non-toxic / direct-gap.
    Returns ``{"count": N, "materials": [...]}``.

    Args:
        exclude_toxic: drop Pb, Cd, As, Hg, Tl, Be compounds.
        require_direct_gap: only direct-gap semiconductors.
        limit: max candidates.
    """
    return _wrap_list(
        _find_solar_absorber(
            exclude_toxic=exclude_toxic,
            require_direct_gap=require_direct_gap,
            limit=limit,
        )
    )


def find_thermoelectric(
    target_gap: str = "narrow",
    limit: int = 10,
) -> dict[str, Any]:
    """Thermoelectric candidates — narrow-gap, often heavy chalcogenide.

    Use for "thermoelectric", "Seebeck", "ZT", "Peltier". Returns
    ``{"count": N, "materials": [...]}``.

    Args:
        target_gap: "metallic" | "very-narrow" | "narrow".
        limit: max candidates.
    """
    return _wrap_list(_find_thermoelectric(target_gap=target_gap, limit=limit))  # type: ignore[arg-type]


def find_transparent_conductor(
    limit: int = 10,
) -> dict[str, Any]:
    """Wide-gap oxide candidates for TCO applications.

    Returns ``{"count": N, "materials": [...]}``. The agent should note
    in its answer that real conductivity requires extrinsic doping
    that MP does not capture.

    Args:
        limit: max candidates.
    """
    return _wrap_list(_find_transparent_conductor(limit=limit))


# ── Property-sheet expansions ──────────────────────────────────────────────


def get_elastic_properties(material_id: str) -> dict[str, Any]:
    """Bulk modulus, shear modulus, hardness for one mp-id.

    Returns dict with bulk_modulus_vrh_gpa, shear_modulus_vrh_gpa,
    young_modulus_vrh_gpa, poisson_ratio, anisotropy,
    vickers_hardness_gpa (empirical), interpretation. Honestly reports
    when MP has no elasticity data for the entry.
    """
    return _get_elastic_properties(material_id)


def get_electronic_summary(material_id: str) -> dict[str, Any]:
    """Electronic-structure summary: gap, direct/indirect, magnetic ordering.

    Lightweight lookup (doesn't fetch full DOS arrays). Returns
    band_gap_eV, is_gap_direct, vbm/cbm energies, fermi_energy_eV,
    is_magnetic, ordering, total_magnetisation, interpretation.
    """
    return _get_electronic_summary(material_id)


# ── Strict analytics ───────────────────────────────────────────────────────


def compute_phase_diagram_strict(
    chemsys: str,
    include_decomposition: bool = True,
    limit_offhull_examples: int = 5,
) -> dict[str, Any]:
    """Real pymatgen PhaseDiagram math for one chemsys.

    Unlike get_phase_diagram (returns MP's tabulated entries), this
    builds the actual convex hull and computes decomposition products
    + reaction enthalpies for off-hull entries. Use when a user asks
    "what would this decompose into?" or "is X synthesizable as a
    single phase?". Slower than get_phase_diagram (pymatgen-heavy).

    Args:
        chemsys: dash-separated elements ("Li-Fe-O").
        include_decomposition: also report decomposition products for
            the most-stable metastable entries.
        limit_offhull_examples: how many metastable entries to enrich
            with decomposition details.
    """
    return _compute_phase_diagram_strict(
        chemsys,
        include_decomposition=include_decomposition,
        limit_offhull_examples=limit_offhull_examples,
    )


# ── Multi-criteria ranking ─────────────────────────────────────────────────


def pareto_rank(
    material_ids: list[str],
    criteria: list[dict[str, Any]],
    limit: int = 10,
) -> dict[str, Any]:
    """Rank candidates by weighted multi-criteria score, flag Pareto-optimal.

    Use this for the FINAL ranking step when you have a shortlist and
    multiple competing properties. Each criterion is a dict with:
      - property: "band_gap" | "density" | "energy_above_hull" |
        "formation_energy_per_atom"
      - direction: "max" | "min" | "near"
      - target: float (required when direction="near")
      - weight: float (defaults to 1.0)

    Returns ranking sorted by descending weighted score, with each
    entry flagged as Pareto-optimal or not. Lets you justify the
    shortlist to the user with concrete numbers instead of "I picked
    these three because they came first".

    Example:
        pareto_rank(
            material_ids=["mp-149", "mp-66", "mp-690687"],
            criteria=[
                {"property": "band_gap", "direction": "near", "target": 1.5, "weight": 2},
                {"property": "energy_above_hull", "direction": "min", "weight": 1},
            ],
        )
    """
    return _pareto_rank(material_ids, criteria, limit=limit)


# ── JARVIS-DFT (NIST) ──────────────────────────────────────────────────────


def find_2d_materials(
    elements: list[str] | None = None,
    max_exfoliation_energy_meV: float | None = None,
    band_gap_range: tuple[float, float] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search JARVIS-DFT (NIST) for 2D / layered candidates.

    Use when the user asks about monolayers, 2D materials, exfoliation,
    MXene, graphene-class, TMDCs. Materials Project does not cover the
    2D regime; JARVIS does.

    Args:
        elements: must-contain element symbols.
        max_exfoliation_energy_meV: cap on exfoliation energy (mJ/m²);
            < 100 mJ/m² is typically easily exfoliable.
        band_gap_range: (min, max) OptB88vdW band gap in eV.
        limit: max candidates.
    """
    return _find_2d_materials(
        elements=elements,
        max_exfoliation_energy_meV=max_exfoliation_energy_meV,
        band_gap_range=band_gap_range,
        limit=limit,
    )


def get_jarvis_topological() -> dict[str, Any]:
    """List JARVIS-DFT topological materials (TI, Weyl, Dirac semimetals).

    Use when the user asks for topological insulators, Dirac/Weyl
    semimetals, spin-Hall candidates, Z2 invariants. ~600 entries
    classified by symmetry-based topological indicators.
    """
    return _get_jarvis_topological()


# Canonical registry — both MCP server and agent runner iterate over this list.
ALL_TOOLS: list[Callable[..., Any]] = [
    # Property lookup
    search_materials,
    get_material,
    compare_materials,
    check_stability,
    # Synthesis context
    get_phase_diagram,
    predict_decomposition,
    get_competing_phases,
    compute_phase_diagram_strict,
    # Application-tuned discovery (Phase 1 — domain combinators)
    find_battery_anode,
    find_battery_cathode,
    find_solar_absorber,
    find_thermoelectric,
    find_transparent_conductor,
    # Property-sheet expansions
    get_elastic_properties,
    get_electronic_summary,
    # Multi-criteria ranking
    pareto_rank,
    # Computational interop
    get_structure,
    # JARVIS-DFT (NIST) — second DFT source covering 2D / topological
    find_2d_materials,
    get_jarvis_topological,
    # Literature
    get_doi_metadata,
    find_preprints,
]
