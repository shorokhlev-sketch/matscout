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
    # Hand-picked element sets per chemistry — these are the elements
    # that actually appear in published electrode materials. Working
    # ion always included (the agent will see "Li in elements"); host
    # elements are the framework atoms the working ion shuttles between.
    host_elements = {
        "lithium": ["Li", "C", "Si", "Sn", "Ti", "Sb", "Ge", "Al", "O"],
        "sodium": ["Na", "C", "Sn", "Ti", "Sb", "Ge", "P", "O"],
        "magnesium": ["Mg", "Mo", "S", "Se", "O", "Ti"],
        "potassium": ["K", "C", "Sn", "Sb", "Bi", "O"],
    }[chemistry]

    filters = SearchFilters(
        elements=host_elements,
        exclude_elements=["U", "Th", "Pa", "Np", "Pu", "Am", "Cm"],  # radioactives
        max_energy_above_hull=0.05,
        num_elements=(1, 4),
        limit=limit * 4,  # over-fetch, then post-filter
    )
    results = search_materials(filters=filters)

    # Post-filter: must contain the working ion OR be a known anode
    # host (graphite, Si, Sn, etc.). Drops candidates that match an
    # element from the union but aren't actually anodes.
    working_ion = {"lithium": "Li", "sodium": "Na", "magnesium": "Mg", "potassium": "K"}[chemistry]
    known_hosts = {"C", "Si", "Sn", "Sb", "Ge", "Ti", "Mo"}
    kept = [
        c
        for c in results
        if working_ion in (c.elements or [])
        or (c.nelements == 1 and (c.elements or [""])[0] in known_hosts)
    ]
    return kept[:limit]


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

    filters = SearchFilters(
        elements=[working_ion, *transition_metals, "O", "P", "S", "F"],
        exclude_elements=["U", "Th", "Pa", "Np", "Pu"],
        max_energy_above_hull=0.03,
        num_elements=(3, 5),  # cathodes are at minimum ternary (Li-TM-O)
        limit=limit * 3,
    )
    results = search_materials(filters=filters)

    # Must contain working ion AND at least one transition metal.
    kept = [
        c
        for c in results
        if working_ion in (c.elements or [])
        and any(tm in (c.elements or []) for tm in transition_metals)
    ]
    return kept[:limit]


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
    filters = SearchFilters(
        band_gap_range=bg,
        density_range=(5.0, 15.0),  # heavy-element bias
        only_stable=True,
        # Bias toward chalcogenides and heavy pnictides — the proven
        # thermoelectric chemistries (Bi-Te, Pb-Te, Sb-Te, Ag-Sb-Te, …).
        elements=["S", "Se", "Te", "Sb", "Bi", "Pb", "Sn", "Ag", "Cu"],
        num_elements=(2, 5),
        limit=limit * 2,
    )
    return search_materials(filters=filters)[:limit]


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
    filters = SearchFilters(
        elements=["O", "In", "Sn", "Zn", "Cd", "Ga", "Al", "Mg", "Si"],
        band_gap_range=(3.0, 6.0),
        only_stable=True,
        is_metal=False,
        num_elements=(2, 4),
        limit=limit * 2,
    )
    return search_materials(filters=filters)[:limit]
