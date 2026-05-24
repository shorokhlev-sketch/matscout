"""Application-aware search wrappers — pre-tuned filters for common asks.

Hard-coded materials-science domain knowledge so the agent stops
inventing wrong filters from scratch on every "find me a battery
anode" prompt. Each function builds a sensible ``SearchFilters`` for
its application and dispatches through ``search_materials``.

The agent should reach for these any time the user names a real
application; only fall back to raw ``search_materials`` when the ask is
genuinely off-template (custom property combination, novel
application). Every combinator returns the same ``list[Candidate]``
shape as ``search_materials``, so the downstream agent code (compare,
check_stability, …) keeps working.

References — what each filter is anchored on:
  - Battery anodes / cathodes: Park 2010 *Journal of Power Sources*
    review on insertion-host design principles + the Materials Project
    battery dashboard tag set.
  - Solar absorbers: Shockley-Queisser 1961 single-junction limit (band
    gap 1.1-1.4 eV optimal). Toxicity exclusions follow RoHS / Cd-free
    photovoltaic guidance.
  - Thermoelectric: Snyder & Toberer 2008 *Nat. Mater.* "complex
    thermoelectric materials" — heavy chalcogenides, narrow gap, complex
    cells suppress lattice thermal conductivity.
  - Transparent conductor: Hosono 2007 *Thin Solid Films* TCO review
    — wide-gap (>3 eV) doped oxides, here approximated as wide-gap
    metallic candidates (out of TCO sweet spot but the best MP can do
    without doping data).
"""

from __future__ import annotations

from typing import Literal

from matscout.models import Candidate, SearchFilters
from matscout.tools.search import search_materials


def find_battery_anode(
    chemistry: Literal["lithium", "sodium", "magnesium", "potassium"] = "lithium",
    limit: int = 10,
) -> list[Candidate]:
    """Pre-tuned anode candidates for solid-state batteries.

    Looks for light-element intercalation hosts and conversion-type
    anodes known to be on the convex hull or near it. Filters by the
    set of elements that commercial and research-grade anodes actually
    use, so the agent doesn't return Actinium when asked for a Li-ion
    anode.

    Args:
        chemistry: which working ion (lithium / sodium / magnesium /
            potassium). Selects the appropriate element shortlist.
        limit: max candidates.

    Returns:
        ``list[Candidate]`` ranked by ``energy_above_hull``.
    """
    # MP's `elements` filter is conjunctive ("must contain ALL"), so
    # we can't OR over an element shortlist in one call. Run two
    # complementary searches and merge: (a) materials containing the
    # working ion, plus (b) pure-element anodes (graphite, Si, Sn…).
    working_ion = {"lithium": "Li", "sodium": "Na", "magnesium": "Mg", "potassium": "K"}[chemistry]
    pure_hosts = {
        "lithium": ["C", "Si", "Sn", "Ge", "Sb"],
        "sodium": ["C", "Sn", "Sb"],
        "magnesium": ["Mg"],
        "potassium": ["C"],
    }[chemistry]
    radioactive_exclude = ["U", "Th", "Pa", "Np", "Pu", "Am", "Cm"]

    ion_filters = SearchFilters(
        elements=[working_ion],
        exclude_elements=radioactive_exclude,
        max_energy_above_hull=0.05,
        num_elements=(2, 4),
        limit=limit * 4,
    )
    results: list[Candidate] = list(search_materials(filters=ion_filters))

    # Pure-element anodes (stable ground state only).
    for host in pure_hosts:
        pure_filters = SearchFilters(
            formula=host,
            only_stable=True,
            num_elements=1,
            limit=2,
        )
        results.extend(search_materials(filters=pure_filters))

    # De-duplicate by material_id, preserve insertion order, then rank.
    seen: dict[str, Candidate] = {}
    for c in results:
        if c.material_id not in seen:
            seen[c.material_id] = c
    ranked = sorted(seen.values(), key=lambda c: c.energy_above_hull or 1e9)
    return ranked[:limit]


def find_battery_cathode(
    chemistry: Literal["lithium", "sodium"] = "lithium",
    limit: int = 10,
) -> list[Candidate]:
    """Pre-tuned cathode candidates (intercalation oxides / phosphates).

    Filters to mixed-valence transition-metal compounds containing the
    working ion, biased toward stable on-hull entries.

    Args:
        chemistry: working ion.
        limit: max candidates.
    """
    transition_metals = ["Co", "Ni", "Mn", "Fe", "V", "Cr", "Cu", "Ti"]
    working_ion = {"lithium": "Li", "sodium": "Na"}[chemistry]

    # MP elements filter is conjunctive — one search per TM, then merge.
    # Each search asks "must contain Li AND <TM> AND O" which catches
    # the real cathode chemistry (Li-Co-O, Li-Ni-O, Li-Mn-O, …).
    results: list[Candidate] = []
    for tm in transition_metals:
        filters = SearchFilters(
            elements=[working_ion, tm, "O"],
            exclude_elements=["U", "Th", "Pa", "Np", "Pu"],
            max_energy_above_hull=0.03,
            num_elements=(3, 5),
            limit=5,
        )
        results.extend(search_materials(filters=filters))
    # Also LiFePO4-class phosphates / fluorides
    for anion in ("P", "F", "S"):
        filters = SearchFilters(
            elements=[working_ion, "Fe", anion, "O"],
            exclude_elements=["U", "Th", "Pa", "Np", "Pu"],
            max_energy_above_hull=0.03,
            num_elements=(3, 5),
            limit=3,
        )
        results.extend(search_materials(filters=filters))

    seen: dict[str, Candidate] = {}
    for c in results:
        if c.material_id not in seen:
            seen[c.material_id] = c
    ranked = sorted(seen.values(), key=lambda c: c.energy_above_hull or 1e9)
    return ranked[:limit]


def find_solar_absorber(
    exclude_toxic: bool = True,
    require_direct_gap: bool = False,
    limit: int = 10,
) -> list[Candidate]:
    """Single-junction solar absorbers near the Shockley-Queisser optimum.

    Band gap 1.1-1.7 eV (broad enough to cover Si, CIGS, perovskites);
    stable; optionally non-toxic (excludes Pb, Cd, As, Hg) and/or
    direct-gap only.

    Args:
        exclude_toxic: drop Pb, Cd, As, Hg compounds (Cd-free PV bias).
        require_direct_gap: only direct-gap semiconductors.
        limit: max candidates.
    """
    toxic = ["Pb", "Cd", "As", "Hg", "Tl", "Be"]
    filters = SearchFilters(
        band_gap_range=(1.1, 1.7),
        exclude_elements=toxic if exclude_toxic else None,
        only_stable=True,
        is_metal=False,
        is_gap_direct=True if require_direct_gap else None,
        limit=limit * 2,
    )
    return search_materials(filters=filters)[:limit]


def find_thermoelectric(
    target_gap: Literal["narrow", "very-narrow", "metallic"] = "narrow",
    limit: int = 10,
) -> list[Candidate]:
    """Thermoelectric candidates — narrow-gap, often heavy chalcogenide.

    Snyder-Toberer design rules: narrow band gap (carrier-rich at room
    T) + heavy elements (low phonon group velocity) + complex unit cell
    (phonon scattering). Approximated here by band-gap range + element
    bias toward S, Se, Te chalcogenides + density floor.

    Args:
        target_gap: "metallic" (0 eV), "very-narrow" (0-0.1 eV — Bi2Te3
            class), or "narrow" (0-0.3 eV, broader). Default narrow.
        limit: max candidates.
    """
    bg = {
        "metallic": (0.0, 0.01),
        "very-narrow": (0.0, 0.1),
        "narrow": (0.0, 0.3),
    }[target_gap]
    # MP elements is conjunctive — run one search per heavy chalcogen
    # / pnictide and merge. Catches the proven thermoelectric families:
    # Bi-Te, Pb-Te, Sb-Te, Ag-Sb-Te, etc.
    chalc_families = ["Te", "Se", "S", "Sb", "Bi"]
    results: list[Candidate] = []
    for x in chalc_families:
        filters = SearchFilters(
            band_gap_range=bg,
            density_range=(5.0, 15.0),
            only_stable=True,
            elements=[x],
            num_elements=(2, 4),
            limit=10,
        )
        results.extend(search_materials(filters=filters))
    seen: dict[str, Candidate] = {}
    for c in results:
        if c.material_id not in seen:
            seen[c.material_id] = c
    ranked = sorted(seen.values(), key=lambda c: c.density or 0, reverse=True)
    return ranked[:limit]


def find_transparent_conductor(
    limit: int = 10,
) -> list[Candidate]:
    """Wide band-gap candidates aimed at transparent-conducting applications.

    Honest caveat: true TCOs (ITO, AZO, FTO) get their conductivity
    from extrinsic doping, which MP does not store. This wrapper
    surfaces undoped wide-gap oxides that have been used as TCO
    parents — the agent should note in its answer that conductivity
    requires doping that isn't captured by these results.

    Args:
        limit: max candidates.
    """
    # Same conjunctive-elements caveat. Search per common TCO-parent
    # element (In, Sn, Zn, Ga, Al) constrained to oxides.
    parent_metals = ["In", "Sn", "Zn", "Ga", "Al"]
    results: list[Candidate] = []
    for m in parent_metals:
        filters = SearchFilters(
            elements=[m, "O"],
            band_gap_range=(3.0, 6.0),
            only_stable=True,
            is_metal=False,
            num_elements=(2, 4),
            limit=5,
        )
        results.extend(search_materials(filters=filters))
    seen: dict[str, Candidate] = {}
    for c in results:
        if c.material_id not in seen:
            seen[c.material_id] = c
    ranked = sorted(seen.values(), key=lambda c: c.energy_above_hull or 1e9)
    return ranked[:limit]


