"""Property-sheet expansions over MP - elastic + electronic.

These surface MP fields that the canonical Candidate doesn't carry but
that materials scientists routinely ask about. Both are read-only
lookups; results are cached.
"""

from __future__ import annotations

from typing import Any

from matscout.tools._client import get_cache, get_client


def get_elastic_properties(material_id: str) -> dict[str, Any]:
    """Bulk, shear, Young's modulus + derived numbers for one mp-id.

    Pulled from MP's elasticity endpoint. Not every entry has elasticity
    data - when MP returns nothing, we say so honestly instead of
    inventing values.

    Returns:
        ``{
            "material_id": str,
            "available": bool,
            "bulk_modulus_vrh_gpa": float | None,    # Voigt-Reuss-Hill
            "shear_modulus_vrh_gpa": float | None,
            "young_modulus_vrh_gpa": float | None,
            "poisson_ratio": float | None,
            "anisotropy": float | None,              # universal anisotropy index
            "vickers_hardness_gpa": float | None,    # Chen-Niu empirical
            "interpretation": str,
        }``

    The Vickers hardness uses the Chen et al. 2011 empirical estimate
    H = 0.92 * (G/B)^1.137 * G^0.708 - give-or-take 30% but useful as
    an order-of-magnitude check.
    """
    cache = get_cache()
    cached = cache.get("get_elastic_properties", {"material_id": material_id})
    if cached is not None:
        return cached

    client = get_client()
    docs = list(client.materials.elasticity.search(material_ids=[material_id]))
    if not docs:
        result = {
            "material_id": material_id,
            "available": False,
            "interpretation": (
                "Materials Project has no elasticity data for this entry: "
                "the elastic tensor was not computed in the DFT workflow. "
                "Try a closely related mp-id, or compute it externally with "
                "VASP / Quantum ESPRESSO using the structure from get_structure."
            ),
            "bulk_modulus_vrh_gpa": None,
            "shear_modulus_vrh_gpa": None,
            "young_modulus_vrh_gpa": None,
            "poisson_ratio": None,
            "anisotropy": None,
            "vickers_hardness_gpa": None,
        }
        cache.put("get_elastic_properties", {"material_id": material_id}, result)
        return result

    d = docs[0]
    k_vrh = getattr(d, "bulk_modulus", None)
    g_vrh = getattr(d, "shear_modulus", None)
    # MP nests these in a {"voigt", "reuss", "vrh"} dict.
    if isinstance(k_vrh, dict):
        k_vrh = k_vrh.get("vrh")
    if isinstance(g_vrh, dict):
        g_vrh = g_vrh.get("vrh")
    k_vrh = float(k_vrh) if k_vrh is not None else None
    g_vrh = float(g_vrh) if g_vrh is not None else None

    young = None
    poisson = None
    if k_vrh is not None and g_vrh is not None and (3 * k_vrh + g_vrh) > 0:
        young = round(9 * k_vrh * g_vrh / (3 * k_vrh + g_vrh), 2)
        poisson = round((3 * k_vrh - 2 * g_vrh) / (2 * (3 * k_vrh + g_vrh)), 3)

    hardness = None
    if k_vrh and g_vrh and k_vrh > 0 and g_vrh > 0:
        ratio = g_vrh / k_vrh
        hardness = round(0.92 * (ratio**1.137) * (g_vrh**0.708), 2)

    aniso = getattr(d, "universal_anisotropy", None)
    if aniso is None:
        aniso = getattr(d, "elastic_anisotropy", None)
    aniso = float(aniso) if aniso is not None else None

    # Interpretation: bucket bulk modulus into rough materials classes
    # so the agent has something domain-aware to say without re-deriving
    # textbook ranges each turn.
    if k_vrh is None:
        interpretation = "Bulk modulus not available."
    elif k_vrh < 30:
        interpretation = "Very compressible (typical of organics, soft alkali salts)."
    elif k_vrh < 80:
        interpretation = "Moderately stiff (most binary oxides, perovskites)."
    elif k_vrh < 200:
        interpretation = "Stiff ceramic / hard metal regime."
    elif k_vrh < 400:
        interpretation = "Superhard precursor regime (transition-metal carbides, nitrides)."
    else:
        interpretation = "Diamond-class superhard."

    result = {
        "material_id": material_id,
        "available": True,
        "bulk_modulus_vrh_gpa": k_vrh,
        "shear_modulus_vrh_gpa": g_vrh,
        "young_modulus_vrh_gpa": young,
        "poisson_ratio": poisson,
        "anisotropy": aniso,
        "vickers_hardness_gpa": hardness,
        "interpretation": interpretation,
    }
    cache.put("get_elastic_properties", {"material_id": material_id}, result)
    return result


def get_electronic_summary(material_id: str) -> dict[str, Any]:
    """Electronic-structure summary: gap, direct/indirect, magnetic ordering.

    Lightweight lookup - does NOT fetch the full DOS / band-structure
    arrays (those are megabyte-scale). Returns the metadata an agent
    needs to characterise a material electronically.

    Returns:
        ``{
            "material_id": str,
            "available": bool,
            "band_gap_eV": float | None,
            "is_gap_direct": bool | None,
            "vbm_energy_eV": float | None,    # valence-band maximum
            "cbm_energy_eV": float | None,    # conduction-band minimum
            "fermi_energy_eV": float | None,
            "is_magnetic": bool | None,
            "ordering": str | None,           # "FM", "AFM", "NM"
            "total_magnetisation_mub": float | None,
            "interpretation": str,
        }``
    """
    cache = get_cache()
    cached = cache.get("get_electronic_summary", {"material_id": material_id})
    if cached is not None:
        return cached

    client = get_client()
    # The summary endpoint already carries the key electronic fields -
    # cheaper than electronic_structure, with the same level of detail
    # for our purposes.
    docs = list(
        client.materials.summary.search(
            material_ids=[material_id],
            fields=[
                "material_id",
                "band_gap",
                "is_gap_direct",
                "vbm",
                "cbm",
                "efermi",
                "is_magnetic",
                "ordering",
                "total_magnetization",
            ],
        )
    )
    if not docs:
        result = {
            "material_id": material_id,
            "available": False,
            "interpretation": "Materials Project has no entry for this mp-id.",
            "band_gap_eV": None,
            "is_gap_direct": None,
            "vbm_energy_eV": None,
            "cbm_energy_eV": None,
            "fermi_energy_eV": None,
            "is_magnetic": None,
            "ordering": None,
            "total_magnetisation_mub": None,
        }
        cache.put("get_electronic_summary", {"material_id": material_id}, result)
        return result

    d = docs[0]
    bg = getattr(d, "band_gap", None)
    is_direct = getattr(d, "is_gap_direct", None)
    vbm = getattr(d, "vbm", None)
    cbm = getattr(d, "cbm", None)
    fermi = getattr(d, "efermi", None)
    is_mag = getattr(d, "is_magnetic", None)
    ordering = getattr(d, "ordering", None)
    mtot = getattr(d, "total_magnetization", None)

    bg = float(bg) if bg is not None else None
    if bg is None:
        electronic = "Band gap unknown."
    elif bg < 0.05:
        electronic = "Metallic (Fermi level inside a band)."
    elif bg < 1.0:
        electronic = "Narrow-gap semiconductor: thermoelectric / IR-detector regime."
    elif bg < 2.5:
        electronic = "Mid-gap semiconductor: photovoltaic / LED active layer regime."
    else:
        electronic = "Wide-gap semiconductor / insulator: UV optoelectronics / dielectric."

    if is_mag:
        electronic += f" Magnetic ordering: {ordering or '?'}."
        if isinstance(mtot, int | float) and abs(mtot) > 0.05:
            electronic += f" Total moment ~{round(float(mtot), 2)} μB/cell."

    result = {
        "material_id": material_id,
        "available": True,
        "band_gap_eV": bg,
        "is_gap_direct": bool(is_direct) if is_direct is not None else None,
        "vbm_energy_eV": float(vbm) if vbm is not None else None,
        "cbm_energy_eV": float(cbm) if cbm is not None else None,
        "fermi_energy_eV": float(fermi) if fermi is not None else None,
        "is_magnetic": bool(is_mag) if is_mag is not None else None,
        "ordering": str(ordering) if ordering else None,
        "total_magnetisation_mub": float(mtot) if mtot is not None else None,
        "interpretation": electronic,
    }
    cache.put("get_electronic_summary", {"material_id": material_id}, result)
    return result
