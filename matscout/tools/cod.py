"""COD — Crystallography Open Database, experimental crystal structures.

COD (https://www.crystallography.net/cod/) is a ~500K-entry open
collection of EXPERIMENTAL inorganic + organic + organometallic
crystal structures, refined from single-crystal or powder X-ray /
neutron diffraction. Unlike MP / AFLOW / JARVIS — which describe
relaxed structures from DFT — COD is ground-truth from the lab.

This makes it the right cross-source check when:
  - DFT predicts a phase that isn't observed experimentally
  - You want to compare a calculated lattice constant against a real one
  - The user asks "has this been synthesized?"

We hit COD via its OPTIMADE endpoint (same protocol matscout uses for
the federation tool) plus a fallback to its REST search for keyword
matches that OPTIMADE's element-filter doesn't cover well.
"""

from __future__ import annotations

from typing import Any

import httpx

from matscout.tools._client import get_cache

_USER_AGENT = "matscout/0.1 (+https://matscout.prfo.design)"
_HTTP_TIMEOUT = 15.0
_COD_OPTIMADE = "https://www.crystallography.net/cod/optimade/v1"


def find_cod_experimental(
    *,
    elements: list[str] | None = None,
    chemical_formula: str | None = None,
    space_group_number: int | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Search COD for experimentally-refined crystal structures.

    Args:
        elements: must-contain element symbols (e.g. ``["Fe", "O"]``).
        chemical_formula: reduced formula string (``"Fe2O3"``).
        space_group_number: International space-group number (1-230).
        limit: max entries to return.

    Returns:
        ``{
            "source": "cod",
            "filter": str,
            "count": int,
            "entries": [
                {"cod_id", "formula", "elements", "spacegroup_number",
                 "nsites", "cod_url"}, ...
            ],
            "note": str
        }``
    """
    parts: list[str] = []
    if elements:
        elems = [e.strip().title() for e in elements if e.strip()]
        if elems:
            element_clauses = " AND ".join(f'elements HAS "{el}"' for el in elems)
            parts.append(f"({element_clauses})")
    if chemical_formula:
        parts.append(f'chemical_formula_reduced="{chemical_formula.strip()}"')
    if space_group_number is not None:
        parts.append(f"_cod_spacegroup_number={int(space_group_number)}")
    if not parts:
        return {
            "source": "cod",
            "filter": "",
            "count": 0,
            "entries": [],
            "note": "Need at least one of: elements, chemical_formula, space_group_number.",
        }
    filt = " AND ".join(parts)

    cache_args = {"filter": filt, "limit": limit}
    cache = get_cache()
    cached = cache.get("find_cod_experimental", cache_args)
    if cached is not None:
        return cached

    safe = filt.replace(" ", "%20").replace('"', "%22")
    url = f"{_COD_OPTIMADE}/structures?filter={safe}&page_limit={limit}"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as c:
            resp = c.get(url, headers={"User-Agent": _USER_AGENT})
    except (httpx.HTTPError, OSError) as e:
        return {
            "source": "cod",
            "filter": filt,
            "count": 0,
            "entries": [],
            "note": f"COD unreachable: {type(e).__name__}: {e}",
        }
    if resp.status_code != 200:
        return {
            "source": "cod",
            "filter": filt,
            "count": 0,
            "entries": [],
            "note": f"COD returned HTTP {resp.status_code}",
        }

    body = resp.json()
    rows = body.get("data") or []
    entries: list[dict[str, Any]] = []
    for r in rows:
        attrs = r.get("attributes") or {}
        cod_id = r.get("id") or ""
        entries.append(
            {
                "cod_id": cod_id,
                "formula": attrs.get("chemical_formula_descriptive")
                or attrs.get("chemical_formula_reduced"),
                "elements": attrs.get("elements") or [],
                "spacegroup_number": attrs.get("_cod_spacegroup_number"),
                "nsites": attrs.get("nsites"),
                "cod_url": (
                    f"https://www.crystallography.net/cod/{cod_id.split(':')[-1]}.html"
                    if cod_id
                    else None
                ),
            }
        )

    result = {
        "source": "cod",
        "filter": filt,
        "count": len(entries),
        "entries": entries,
        "note": (
            "COD entries are EXPERIMENTAL refinements (single-crystal or "
            "powder diffraction). Use to cross-check DFT predictions against "
            "real-world ground truth; expect ~1-3% lattice-constant mismatch "
            "between COD and the DFT databases for normal-valence ionic solids."
        ),
    }
    cache.put("find_cod_experimental", cache_args, result)
    return result
