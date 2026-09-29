"""JARVIS-DFT (NIST) integration via OPTIMADE.

NIST's static-dump endpoints at jarvis.nist.gov/static/jarvisdft/*.json
returned 502 in mid-2026 and have been intermittent since. The same
JARVIS data is reachable through JARVIS's OPTIMADE service at
jarvis.nist.gov/optimade/jarvisdft/v1, which is the path we use.

The OPTIMADE endpoint only exposes the structural-attribute subset of
JARVIS (elements, formula, spacegroup, lattice). It does NOT carry the
custom JARVIS fields - 2D exfoliation energy, OptB88vdW band gap,
topological-invariant classification - because those aren't part of
the OPTIMADE schema. So the previous fine-grained ``find_2d_materials``
and ``get_jarvis_topological`` filters degrade to "JARVIS structures
matching an element pattern". The agent should reach for
``optimade_search(providers=['jarvis'], ...)`` for queries that need
the full OPTIMADE feature set.
"""

from __future__ import annotations

from typing import Any

import httpx

from matscout.tools._client import get_cache

_USER_AGENT = "matscout/0.1 (+https://matscout.prfo.design)"
_HTTP_TIMEOUT = 12.0
_JARVIS_OPTIMADE = "https://jarvis.nist.gov/optimade/jarvisdft/v1"


def _optimade_query(optimade_filter: str, page_limit: int) -> dict[str, Any]:
    """Single OPTIMADE call to JARVIS, graceful on failure."""
    cache = get_cache()
    cache_args = {"filter": optimade_filter, "page_limit": page_limit}
    cached = cache.get("_jarvis_optimade", cache_args)
    if cached is not None:
        return cached

    safe = optimade_filter.replace(" ", "%20").replace('"', "%22")
    url = f"{_JARVIS_OPTIMADE}/structures?filter={safe}&page_limit={page_limit}"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as c:
            resp = c.get(url, headers={"User-Agent": _USER_AGENT})
    except (httpx.HTTPError, OSError) as e:
        return {"available": False, "note": f"network error: {e}", "structures": []}
    if resp.status_code != 200:
        return {
            "available": False,
            "note": f"HTTP {resp.status_code} from JARVIS OPTIMADE",
            "structures": [],
        }
    try:
        body = resp.json()
    except ValueError as e:
        return {"available": False, "note": f"non-JSON: {e}", "structures": []}

    rows = body.get("data") or []
    structs: list[dict[str, Any]] = []
    for r in rows:
        attrs = r.get("attributes") or {}
        jid = r.get("id") or ""
        structs.append(
            {
                "jid": jid,
                "formula": attrs.get("chemical_formula_descriptive")
                or attrs.get("chemical_formula_reduced"),
                "elements": attrs.get("elements") or [],
                "spacegroup": attrs.get("_jarvis_spg_symbol") or attrs.get("space_group_it_number"),
                "nelements": attrs.get("nelements"),
                "nsites": attrs.get("nsites"),
                "jarvis_url": (
                    f"https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{jid.replace('jarvis:', '')}.xml"
                    if jid
                    else None
                ),
            }
        )
    result = {"available": True, "note": None, "structures": structs}
    cache.put("_jarvis_optimade", cache_args, result)
    return result


def find_2d_materials(
    *,
    elements: list[str] | None = None,
    max_exfoliation_energy_meV: float | None = None,
    band_gap_range: tuple[float, float] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search JARVIS for materials (via OPTIMADE).

    Filters by element shortlist; exfoliation-energy and band-gap
    filters are accepted for API compatibility but not enforced (the
    OPTIMADE schema doesn't expose those JARVIS-custom fields).
    """
    if not elements:
        return {
            "source": "jarvis-dft",
            "table": "2D materials",
            "available": False,
            "count": 0,
            "materials": [],
            "note": (
                "JARVIS OPTIMADE needs an element shortlist to query. "
                "Pass elements=[...] or use optimade_search with a "
                "broader filter."
            ),
        }
    filt = " AND ".join(f'elements HAS "{e.strip().title()}"' for e in elements)
    raw = _optimade_query(filt, limit)
    return {
        "source": "jarvis-dft",
        "table": "2D materials",
        "available": raw["available"],
        "count": len(raw["structures"]),
        "materials": raw["structures"],
        "note": (
            raw.get("note")
            or "JARVIS OPTIMADE returns structural metadata only; exfoliation-"
            "energy / band-gap filters are not enforced via this endpoint. "
            "Use Materials Project + COD cross-source for richer property "
            "filtering."
        ),
    }


def get_jarvis_topological() -> dict[str, Any]:
    """JARVIS-classified topological materials.

    The OPTIMADE endpoint doesn't expose the topological-class field
    directly. We return a no-data payload so the agent doesn't waste
    a tool call here - for actual topological cross-validation, search
    arXiv via find_preprints for the candidate's mp-id + 'topological'.
    """
    return {
        "source": "jarvis-dft",
        "table": "topological",
        "available": False,
        "count": 0,
        "materials": [],
        "note": (
            "JARVIS's topological-classification table is not exposed via "
            "the OPTIMADE endpoint we currently have access to (NIST's "
            "static figshare dump is HTTP 502). For topological cross-"
            "validation, search arXiv via find_preprints("
            "'<material_id> topological insulator', max_age_days=730) "
            "or query the symmetry indicators table through "
            "Topological Materials Database (Bernevig 2019) which is not "
            "yet a matscout tool."
        ),
    }
