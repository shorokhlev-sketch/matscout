"""OPTIMADE federated search across multiple materials databases.

OPTIMADE (https://optimade.org/) is a standardised REST protocol that
10+ materials databases speak: Materials Project, AFLOW, COD, JARVIS-
DFT, NOMAD, MPDS, AiiDA, Materials Cloud, and more. One filter
expression, one results shape, many sources.

This tool issues the SAME query against several providers in parallel
and merges + tags the results. The agent gets cross-source coverage
without having to know each database's idiosyncrasies. Useful when:

  - You need redundancy / cross-validation across DFT databases
    (catch entries that MP missed but OQMD has, or vice versa).
  - You want experimental ground-truth alongside DFT predictions
    (COD ≈ experimental refinements; MP / AFLOW / JARVIS ≈ DFT).
  - You want broader coverage of structural / topological / 2D data.

Filter syntax is OPTIMADE's filter language, e.g.
``elements HAS "Si" AND nelements=1`` or
``chemical_formula_reduced="Fe2O3"``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from matscout.tools._client import get_cache

# Curated provider list. Stable + reachable from typical hosts. Each
# entry is (id, label, base_url) — base_url is the OPTIMADE v1 root.
_PROVIDERS: dict[str, tuple[str, str]] = {
    "mp": (
        "Materials Project",
        "https://optimade.materialsproject.org/v1",
    ),
    "cod": (
        "Crystallography Open Database",
        "https://www.crystallography.net/cod/optimade/v1",
    ),
    "nomad": (
        "NOMAD",
        "https://nomad-lab.eu/prod/v1/optimade/v1",
    ),
    "alexandria": (
        "Alexandria (Bochum)",
        "https://alexandria.icams.rub.de/pbe/v1",
    ),
    "jarvis": (
        "JARVIS-DFT (NIST)",
        "https://jarvis.nist.gov/optimade/jarvisdft/v1",
    ),
    "odbx": (
        "Open Database of Xtals (odbx)",
        "https://optimade.odbx.science/v1",
    ),
    # Dropped 2026-05: aflow (500), mcloud (404), mpdd (timeout). Re-add
    # when their OPTIMADE endpoints come back up.
}

_USER_AGENT = "matscout/0.1 (+https://matscout.prfo.design)"
_HTTP_TIMEOUT = 12.0


async def _query_one(
    client: httpx.AsyncClient,
    provider_id: str,
    base_url: str,
    optimade_filter: str,
    page_limit: int,
) -> dict[str, Any]:
    """Issue one OPTIMADE query against a single provider, gracefully."""
    url = (
        f"{base_url}/structures"
        f"?filter={httpx.URL.__new__(httpx.URL)._uri_reference.encode if False else ''}"
        f"{optimade_filter}&page_limit={page_limit}"
    )
    # Manual URL build because httpx eagerly encodes spaces inside `filter`
    # in a way some providers reject. Keep it simple — encode commas /
    # operators per OPTIMADE spec.
    safe_filter = optimade_filter.replace(" ", "%20").replace('"', "%22")
    url = f"{base_url}/structures?filter={safe_filter}&page_limit={page_limit}"
    try:
        resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except (httpx.HTTPError, OSError) as e:
        return {
            "provider": provider_id,
            "available": False,
            "note": f"network error: {type(e).__name__}: {e}",
            "structures": [],
        }
    if resp.status_code != 200:
        return {
            "provider": provider_id,
            "available": False,
            "note": f"HTTP {resp.status_code}",
            "structures": [],
        }
    try:
        body = resp.json()
    except ValueError as e:
        return {
            "provider": provider_id,
            "available": False,
            "note": f"non-JSON response: {e}",
            "structures": [],
        }

    rows = body.get("data") or []
    out: list[dict[str, Any]] = []
    for r in rows[:page_limit]:
        attrs = r.get("attributes") or {}
        out.append(
            {
                "provider": provider_id,
                "id": r.get("id"),
                "formula": attrs.get("chemical_formula_descriptive")
                or attrs.get("chemical_formula_reduced"),
                "formula_reduced": attrs.get("chemical_formula_reduced"),
                "elements": attrs.get("elements") or [],
                "nelements": attrs.get("nelements"),
                "nsites": attrs.get("nsites"),
                "spacegroup_number": attrs.get("space_group_it_number")
                or attrs.get("_optimade_spacegroup_number"),
                "lattice_a": attrs.get("lattice_vectors", [[]])[0][0]
                if attrs.get("lattice_vectors") and len(attrs["lattice_vectors"]) >= 1
                else None,
            }
        )
    return {
        "provider": provider_id,
        "available": True,
        "note": None,
        "structures": out,
    }


def optimade_search(
    optimade_filter: str,
    *,
    providers: list[str] | None = None,
    page_limit: int = 5,
) -> dict[str, Any]:
    """Federated OPTIMADE search — query several materials databases at once.

    Issues the same filter against each provider in parallel, returns
    per-provider hit lists tagged by source. Use when you want cross-
    database coverage instead of a single source's view.

    Args:
        optimade_filter: OPTIMADE filter-language string, e.g.
            ``'elements HAS "Si" AND nelements=1'`` (pure Si) or
            ``'chemical_formula_reduced="Fe2O3"'``.
        providers: which providers to query. Defaults to all curated
            providers. Recognised ids: ``mp``, ``aflow``, ``cod``,
            ``jarvis``, ``mcloud``, ``odbx``, ``mpdd``.
        page_limit: max hits per provider (1-50).

    Returns:
        ``{
            "filter": str,
            "providers_queried": [provider_id, ...],
            "by_provider": {provider_id: {"available", "note",
                                          "structures": [...]}},
            "total_hits": int,
            "summary": str,
        }``

    Notes on the data:
        - Each provider returns its own native identifier — MP uses
          ``mp-XXXX``, AFLOW uses ``aflow:auid:...``, COD uses
          ``cod:NNNNNN``, JARVIS uses ``jarvis:JVASP-NNNN``.
        - DFT databases (MP, AFLOW, JARVIS) describe relaxed structures
          at 0 K, 0 GPa. COD entries are EXPERIMENTAL refinements —
          ground truth, often the right cross-check when DFT predicts
          something exotic.
    """
    chosen: list[str] = providers or list(_PROVIDERS.keys())
    bad = [p for p in chosen if p not in _PROVIDERS]
    if bad:
        raise ValueError(f"Unknown OPTIMADE providers: {bad}. Known: {sorted(_PROVIDERS)}")

    cache = get_cache()
    cache_args = {
        "filter": optimade_filter,
        "providers": sorted(chosen),
        "page_limit": page_limit,
    }
    cached = cache.get("optimade_search", cache_args)
    if cached is not None:
        return cached

    async def run_all() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            tasks = [
                _query_one(
                    client,
                    pid,
                    _PROVIDERS[pid][1],
                    optimade_filter,
                    page_limit,
                )
                for pid in chosen
            ]
            return await asyncio.gather(*tasks)

    results = asyncio.run(run_all())

    by_provider: dict[str, dict[str, Any]] = {}
    total_hits = 0
    available_count = 0
    for r in results:
        pid = r["provider"]
        by_provider[pid] = {
            "name": _PROVIDERS[pid][0],
            "available": r["available"],
            "note": r["note"],
            "hits": len(r["structures"]),
            "structures": r["structures"],
        }
        total_hits += len(r["structures"])
        if r["available"]:
            available_count += 1

    summary = (
        f"OPTIMADE federated query across {len(chosen)} providers "
        f"({available_count} responsive); {total_hits} structures total. "
        f"Top providers: "
        + ", ".join(
            f"{pid}={by_provider[pid]['hits']}" for pid in chosen if by_provider[pid]["available"]
        )
    )

    result = {
        "filter": optimade_filter,
        "providers_queried": chosen,
        "by_provider": by_provider,
        "total_hits": total_hits,
        "summary": summary,
    }
    cache.put("optimade_search", cache_args, result)
    return result
