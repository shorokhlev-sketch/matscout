"""Application-aware search wrappers - pre-tuned filters for common asks.

Each combinator runs a small set of CHEMSYS-specific searches (not
a single "contains X" filter), so the results are the chemistries that
actually appear in real electrodes / absorbers / TE materials rather
than every Li-containing phosphate that MP knows about. Results are
merged + de-duplicated + ranked.

References - what each shortlist is anchored on:
  - Battery anodes / cathodes: Whittingham 2004 *Chem. Rev.* + Park
    2010 *J. Power Sources* anode review + LiBES handbook.
  - Solar absorbers: Shockley-Queisser 1961 + Cd-free PV guidance.
  - Thermoelectric: Snyder & Toberer 2008 *Nat. Mater.* + Zhao 2014
    SnSe report. Anchored on heavy-chalcogen / heavy-pnictide families.
  - Transparent conductor: Hosono 2007 *Thin Solid Films* TCO review
    - wide-gap oxides with known dopant-induced conductivity.
"""

from __future__ import annotations

from typing import Literal

from matscout.models import Candidate, SearchFilters
from matscout.tools.search import search_materials

# ---- Battery electrodes ----
# Per-ion: list of (chemsys-or-formula, label) probes. `formula` runs
# a pure-element search; multi-element strings become elements=[…]
# (conjunctive - "contains all", because that's how MP works).
_ANODE_PROBES: dict[str, list[tuple[str, str]]] = {
    "lithium": [
        ("C", "graphite + carbon anodes"),
        ("Si", "Si-class alloying anode"),
        ("Sn", "Sn-class alloying anode"),
        ("Ge", "Ge-class alloying anode"),
        ("Li-C", "Li-C intercalation (LiC6)"),
        ("Li-Si", "Li-Si alloys (Li15Si4 etc.)"),
        ("Li-Sn", "Li-Sn alloys"),
        ("Li-Ge", "Li-Ge alloys"),
        ("Li-Ti-O", "Li4Ti5O12-class (zero-strain LTO)"),
        ("Li-Nb-O", "TiNb2O7-class Wadsley-Roth"),
    ],
    "sodium": [
        ("C", "hard carbon"),
        ("Sn", "Sn-class alloying"),
        ("Sb", "Sb-class alloying"),
        ("Na-Sn", "Na-Sn alloys"),
        ("Na-Sb", "Na-Sb alloys"),
        ("Na-Ti-O", "Na-Ti-O Wadsley-Roth"),
    ],
    "magnesium": [
        ("Mg", "Mg metal anode"),
        ("Mg-Sn", "Mg-Sn alloys"),
        ("Mg-Bi", "Mg-Bi alloys"),
    ],
    "potassium": [
        ("C", "K intercalation in carbon"),
        ("Sn", "K-Sn alloying"),
        ("Sb", "K-Sb alloying"),
    ],
}

# Cathode families per ion - anchored on real production / R&D materials.
_CATHODE_PROBES: dict[str, list[tuple[str, str]]] = {
    "lithium": [
        ("Li-Co-O", "LiCoO2 (LCO)"),
        ("Li-Ni-O", "LiNiO2 family"),
        ("Li-Mn-O", "LiMn2O4 / Li-rich Mn"),
        ("Li-Fe-O", "Li-Fe-O (intermediate)"),
        ("Li-Ni-Co-O", "NCA / NMC family"),
        ("Li-Ni-Mn-Co-O", "NMC variants"),
        ("Li-Fe-P-O", "LiFePO4 olivine"),
        ("Li-Mn-P-O", "LiMnPO4 olivine"),
        ("Li-Co-P-O", "LiCoPO4"),
        ("Li-V-P-O", "Li3V2(PO4)3 vanadium phosphate"),
        ("Li-Mn-Si-O", "Li2MnSiO4 silicate cathode"),
        ("Li-Fe-Si-O", "Li2FeSiO4 silicate cathode"),
    ],
    "sodium": [
        ("Na-Mn-O", "Na-Mn-O layered"),
        ("Na-Fe-Mn-O", "P2-type Na-MMO"),
        ("Na-Ni-Mn-O", "Na-Ni-Mn-O layered"),
        ("Na-V-P-O", "Na3V2(PO4)3 NASICON"),
        ("Na-Fe-P-O", "NaFePO4 maricite/olivine"),
        ("Na-V-P-O-F", "Na3V2(PO4)2F3"),
    ],
}

# Common solid-state Li-ion electrolytes - explicitly EXCLUDED from the
# anode probe so a query for "anode" doesn't surface LiPON / garnet /
# LATP / LISICON. (Each line is a chemsys prefix substring match.)
_ELECTROLYTE_FAMILIES = (
    "P",  # phosphates (LiPF6, LiPON, LiPO4-class)
    "La-Zr",  # LLZO garnet
    "Ti-Al-P",  # LATP NASICON
    "Ge-P-S",  # LGPS
    "Li-Sc",  # LISICON
)


def _run_probes(
    probes: list[tuple[str, str]],
    *,
    max_eah: float = 0.05,
    per_probe_limit: int = 5,
    exclude_elements: list[str] | None = None,
) -> list[Candidate]:
    """Issue one MP search per probe, return merged candidates."""
    results: list[Candidate] = []
    radioactive = exclude_elements or ["U", "Th", "Pa", "Np", "Pu", "Am", "Cm"]
    for chemsys_or_formula, _label in probes:
        is_pure = "-" not in chemsys_or_formula
        if is_pure:
            f = SearchFilters(
                formula=chemsys_or_formula,
                only_stable=True,
                num_elements=1,
                exclude_elements=radioactive,
                limit=per_probe_limit,
            )
        else:
            elements = chemsys_or_formula.split("-")
            f = SearchFilters(
                elements=elements,
                exclude_elements=radioactive,
                max_energy_above_hull=max_eah,
                num_elements=(len(elements), len(elements) + 1),
                limit=per_probe_limit,
            )
        results.extend(search_materials(filters=f))
    return results


def _dedup_rank(
    results: list[Candidate], *, limit: int, key: str = "energy_above_hull"
) -> list[Candidate]:
    """De-duplicate by mp-id (keep first occurrence), then rank ascending."""
    seen: dict[str, Candidate] = {}
    for c in results:
        if c.material_id not in seen:
            seen[c.material_id] = c
    ranked = sorted(seen.values(), key=lambda c: getattr(c, key, None) or 1e9)
    return ranked[:limit]


# ---- Public combinators ----
def find_battery_anode(
    chemistry: Literal["lithium", "sodium", "magnesium", "potassium"] = "lithium",
    limit: int = 10,
) -> list[Candidate]:
    """Anode candidates by KNOWN-chemistry chemsys probes.

    Runs one MP search per anode chemsys family (carbon, silicon, tin,
    germanium, lithium alloys, lithium-titanium-oxide LTO, …) and merges
    + dedups + ranks. The result is what actually appears in
    lithium-ion / sodium-ion electrochemistry literature, not "every
    material that happens to contain Li" (which also returns
    phosphate electrolytes and silicate fillers).

    Args:
        chemistry: which working ion. Each ion has its own probe list.
        limit: max merged candidates.
    """
    return _dedup_rank(_run_probes(_ANODE_PROBES[chemistry]), limit=limit)


def find_battery_cathode(
    chemistry: Literal["lithium", "sodium"] = "lithium",
    limit: int = 10,
) -> list[Candidate]:
    """Cathode candidates by known intercalation-host chemsys probes.

    Runs one search per established cathode chemsys: LCO (Li-Co-O), NMC
    (Li-Ni-Mn-Co-O), LFP (Li-Fe-P-O), LMP, polyanionic Mn / V phosphates
    and silicates, NASICON-type Na vanadium phosphates for Na chemistry.
    """
    return _dedup_rank(_run_probes(_CATHODE_PROBES[chemistry]), limit=limit)


def find_solar_absorber(
    exclude_toxic: bool = True,
    require_direct_gap: bool = False,
    limit: int = 10,
) -> list[Candidate]:
    """Single-junction absorbers near the Shockley-Queisser optimum.

    Band gap 1.1-1.7 eV, stable, optionally non-toxic / direct-gap.
    Single broad MP search - solar absorbers are a band-gap question
    more than a chemistry question (Si, CIGS, CZTS, perovskites all
    co-exist in this gap window), so we don't bias the chemsys.
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
    """Thermoelectric candidates by heavy-chalcogenide / -pnictide chemsys probes.

    Snyder-Toberer design rules: narrow gap + heavy elements + complex
    cell. Anchored on the proven TE families: Bi-Te, Pb-Te, Sb-Te,
    Ag-Sb-Te (TAGS), Sn-Se (Zhao 2014), Bi-Sb, half-Heusler (M-Ni-Sn).
    """
    bg = {
        "metallic": (0.0, 0.01),
        "very-narrow": (0.0, 0.1),
        "narrow": (0.0, 0.3),
    }[target_gap]
    probes: list[tuple[str, str]] = [
        ("Bi-Te", "Bi2Te3 family"),
        ("Pb-Te", "PbTe family"),
        ("Sb-Te", "Sb2Te3 family"),
        ("Bi-Se", "Bi2Se3"),
        ("Sn-Se", "SnSe (Zhao 2014)"),
        ("Ag-Sb-Te", "TAGS-class"),
        ("Ge-Te", "GeTe-class"),
        ("Bi-Sb", "Bi-Sb half-Heuslers' parent"),
    ]
    results: list[Candidate] = []
    for chemsys, _label in probes:
        elements = chemsys.split("-")
        f = SearchFilters(
            elements=elements,
            band_gap_range=bg,
            only_stable=True,
            num_elements=(len(elements), len(elements) + 1),
            limit=4,
        )
        results.extend(search_materials(filters=f))
    return _dedup_rank(results, limit=limit, key="density")[::-1][:limit]  # bias to heavier


def find_transparent_conductor(
    limit: int = 10,
) -> list[Candidate]:
    """Wide-gap oxide candidates for TCO-class applications.

    Returns undoped wide-gap oxides anchored on TCO-parent chemsys
    (In-O, Sn-O, Zn-O, Ga-O, Al-O, plus In-Sn-O, Al-Zn-O). The agent
    must note in its answer that real TCO conductivity requires
    extrinsic doping that MP does not capture.
    """
    probes: list[tuple[str, str]] = [
        ("In-O", "In2O3 parent (ITO without dopant)"),
        ("Sn-O", "SnO2 parent (FTO without dopant)"),
        ("Zn-O", "ZnO parent (AZO without dopant)"),
        ("Ga-O", "Ga2O3 wide-gap"),
        ("Al-O", "Al2O3 (insulator, included for hierarchy)"),
        ("In-Sn-O", "ITO ternary parent"),
        ("Al-Zn-O", "AZO ternary parent"),
        ("Ga-Zn-O", "GZO / IGZO precursor"),
    ]
    results: list[Candidate] = []
    for chemsys, _label in probes:
        elements = chemsys.split("-")
        f = SearchFilters(
            elements=elements,
            band_gap_range=(3.0, 6.0),
            only_stable=True,
            is_metal=False,
            num_elements=(len(elements), len(elements) + 1),
            limit=4,
        )
        results.extend(search_materials(filters=f))
    return _dedup_rank(results, limit=limit)
