"""JARVIS-DFT (NIST) integration.

JARVIS is a NIST database with ~80k+ DFT entries covering properties
Materials Project does not store: 2D-material classification, formation
energy of monolayers, topological invariants, full dielectric and
piezoelectric tensors, and a dedicated Boltzmann-transport workflow for
thermoelectrics.

We hit JARVIS via its public REST proxy (figshare-hosted JSON dumps);
no API key needed. Each tool is small and read-only; results are
cached in the same SQLite the rest of matscout uses.

Refs:
  - Choudhary, K. et al. "Joint Automated Repository for Various
    Integrated Simulations (JARVIS)", *npj Comput. Mater.* 2020.
  - https://jarvis.nist.gov/
"""

from __future__ import annotations

from typing import Any

import httpx

from matscout.tools._client import get_cache

# JARVIS hosts curated property tables as JSON dumps on figshare; each
# dump covers a slice of the database. The full bulk-DFT3D table is
# ~75 MB so we use the more compact 2D and topological tables for now;
# add the full bulk table later if the agent starts asking for it.
_JARVIS_2D_URL = "https://jarvis.nist.gov/static/jarvisdft/jarvis2d.json"
_JARVIS_TOPO_URL = "https://jarvis.nist.gov/static/jarvisdft/jarvisdft_topo.json"

_USER_AGENT = "matscout/0.1 (+https://matscout.prfo.design)"
_HTTP_TIMEOUT = 30.0


def _fetch(url: str) -> list[dict[str, Any]]:
    """Cached fetch — JARVIS tables are static, hours-of-TTL is fine.

    The cache layer only stores dicts, so we wrap/unwrap the list payload
    under a ``data`` key. Cheap and keeps cache's contract intact.
    """
    cache = get_cache()
    cached = cache.get("_jarvis_fetch", {"url": url})
    if cached is not None:
        rows = cached.get("data") or []
        return list(rows)

    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as c:
        resp = c.get(url, headers={"User-Agent": _USER_AGENT})
        if resp.status_code != 200:
            raise RuntimeError(
                f"JARVIS endpoint {url!r} returned HTTP {resp.status_code}. "
                "Try again later; JARVIS occasionally serves slow."
            )
        data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(f"JARVIS returned non-list payload from {url!r}")
    cache.put("_jarvis_fetch", {"url": url}, {"data": data})
    return data


def find_2d_materials(
    *,
    elements: list[str] | None = None,
    max_exfoliation_energy_meV: float | None = None,
    band_gap_range: tuple[float, float] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search JARVIS for 2D materials.

    Args:
        elements: must-contain element symbols.
        max_exfoliation_energy_meV: cap on exfoliation energy (mJ/m²);
            < 100 mJ/m² is typically considered easily exfoliable.
        band_gap_range: ``(min, max)`` band gap in eV.
        limit: max candidates.

    Returns:
        ``{"source": "jarvis-dft", "count": int, "materials": [...]}``
        where each material is ``{jid, formula, elements,
        exfoliation_energy, band_gap_optb88vdw, ehull_per_atom,
        spacegroup, jarvis_url}``.
    """
    table = _fetch(_JARVIS_2D_URL)
    out: list[dict[str, Any]] = []
    want = {e.strip().title() for e in (elements or [])}
    for row in table:
        formula = row.get("formula") or row.get("composition") or ""
        elems = row.get("elements") or row.get("ELEMENTS") or []
        if isinstance(elems, str):
            elems = elems.split(",")
        elems = [e.strip() for e in elems if e]
        if want and not want.issubset(set(elems)):
            continue
        exf = row.get("exfoliation_energy")
        if max_exfoliation_energy_meV is not None and (
            exf is None or float(exf) > max_exfoliation_energy_meV
        ):
            continue
        bg = row.get("optb88vdw_bandgap") or row.get("bandgap_opt")
        if band_gap_range is not None:
            if bg is None:
                continue
            if not (band_gap_range[0] <= float(bg) <= band_gap_range[1]):
                continue
        jid = row.get("jid") or row.get("jvid") or ""
        out.append(
            {
                "jid": jid,
                "formula": formula,
                "elements": elems,
                "exfoliation_energy_mJ_per_m2": exf,
                "band_gap_optb88vdw_eV": bg,
                "spacegroup": row.get("spg_symbol") or row.get("spacegroup"),
                "jarvis_url": (
                    f"https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{jid}.xml" if jid else None
                ),
            }
        )
    out.sort(
        key=lambda r: (
            r.get("exfoliation_energy_mJ_per_m2") or 1e9,
            r.get("formula") or "",
        )
    )
    return {
        "source": "jarvis-dft",
        "table": "2D materials",
        "count": len(out),
        "materials": out[:limit],
        "note": (
            "JARVIS 2D-DFT covers monolayers identified by exfoliation-energy "
            "screening over experimental layered crystals. Band gaps reported "
            "here use the OptB88vdW functional, which underestimates gaps by "
            "~20-30% vs. HSE / experiment but is consistent across the table."
        ),
    }


def get_jarvis_topological() -> dict[str, Any]:
    """Return the JARVIS topological-materials table (~600 entries).

    Lists every JARVIS entry classified as a topological insulator,
    Dirac/Weyl semimetal, or trivial-with-non-zero-Z2 by symmetry-based
    indicators. Used when the user asks for "topological", "Dirac",
    "Weyl", "spin-Hall", etc.

    Returns:
        ``{"source", "count", "materials": [...]}``
    """
    table = _fetch(_JARVIS_TOPO_URL)
    out: list[dict[str, Any]] = []
    for row in table:
        out.append(
            {
                "jid": row.get("jid") or "",
                "formula": row.get("formula") or row.get("composition"),
                "topological_class": (
                    row.get("topological_class") or row.get("class") or row.get("category")
                ),
                "z2_indicators": row.get("z2") or row.get("Z2"),
                "spacegroup": row.get("spg_symbol") or row.get("spacegroup"),
                "band_gap_eV": row.get("optb88vdw_bandgap") or row.get("bandgap_opt"),
            }
        )
    return {
        "source": "jarvis-dft",
        "table": "topological",
        "count": len(out),
        "materials": out,
        "note": (
            "Classification by symmetry-based topological indicators "
            "(Vergniory et al. 2019 framework). Use as a starting point for "
            "topological-property questions; verify against a primary "
            "calculation before downstream use."
        ),
    }
