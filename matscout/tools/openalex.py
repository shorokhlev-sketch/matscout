"""OpenAlex client - 240M+ scholarly works, free REST, no API key.

A stable counterpart to Semantic Scholar (which we disabled because
its anonymous endpoint rate-limits aggressively from cloud egress).
OpenAlex covers the same kind of bibliometric metadata - title,
authors, year, DOI, abstract, cited-by count - but with much friendlier
ungated access and global academic coverage.

Use this whenever the user asks "what's been published about X" or
"recent literature on Y" and you want a journal-style answer (not
just arXiv preprints).
"""

from __future__ import annotations

from typing import Any

import httpx

from matscout.tools._client import get_cache, polite_user_agent

_HTTP_TIMEOUT = 15.0
_BASE = "https://api.openalex.org"


def search_openalex(
    query: str,
    *,
    year_from: int | None = None,
    min_cited_by: int | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Search OpenAlex for papers matching a free-text query.

    Args:
        query: search terms (free text). Searches title + abstract +
            full-text where indexed.
        year_from: minimum publication year (inclusive).
        min_cited_by: minimum citation count (filter out low-impact
            noise). 50 is a reasonable threshold for established work;
            5 for emerging.
        limit: max papers to return (1-25 sensible).

    Returns:
        ``{
            "query": str,
            "total_results": int,
            "count_returned": int,
            "papers": [
                {
                    "title", "authors": [...], "year", "doi",
                    "venue", "cited_by", "abstract", "openalex_id",
                    "is_open_access"
                }
            ],
            "note": str
        }``
    """
    cache_args = {
        "query": query,
        "year_from": year_from,
        "min_cited_by": min_cited_by,
        "limit": limit,
    }
    cache = get_cache()
    cached = cache.get("search_openalex", cache_args)
    if cached is not None:
        return cached

    # OpenAlex filter chain
    filters: list[str] = []
    if year_from is not None:
        filters.append(f"publication_year:>{int(year_from) - 1}")
    if min_cited_by is not None:
        filters.append(f"cited_by_count:>{int(min_cited_by) - 1}")
    params: dict[str, Any] = {
        "search": query,
        "per_page": max(1, min(int(limit), 25)),
        "sort": "cited_by_count:desc",
    }
    if filters:
        params["filter"] = ",".join(filters)

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as c:
            resp = c.get(
                f"{_BASE}/works", params=params, headers={"User-Agent": polite_user_agent()}
            )
    except (httpx.HTTPError, OSError) as e:
        return {
            "query": query,
            "total_results": 0,
            "count_returned": 0,
            "papers": [],
            "note": f"OpenAlex unreachable: {type(e).__name__}: {e}",
        }
    if resp.status_code != 200:
        return {
            "query": query,
            "total_results": 0,
            "count_returned": 0,
            "papers": [],
            "note": f"OpenAlex returned HTTP {resp.status_code}",
        }

    body = resp.json()
    meta = body.get("meta", {})
    total = int(meta.get("count") or 0)
    results = body.get("results") or []

    papers: list[dict[str, Any]] = []
    for w in results:
        # Authorships → flat list of names
        authorships = w.get("authorships") or []
        authors = [(a.get("author") or {}).get("display_name", "?") for a in authorships[:8]]
        # Abstract is inverted-index encoded in OpenAlex (a compact
        # representation). Reassemble into prose for the agent.
        abs_inv = w.get("abstract_inverted_index") or {}
        abstract_text = _abstract_from_inverted_index(abs_inv)

        # Venue / journal - host_venue is being phased out, primary_location
        # is the modern field. Fall back gracefully.
        venue = None
        if w.get("primary_location"):
            src = (w["primary_location"] or {}).get("source") or {}
            venue = src.get("display_name")
        if not venue and w.get("host_venue"):
            venue = (w["host_venue"] or {}).get("display_name")

        papers.append(
            {
                "title": w.get("display_name"),
                "authors": authors,
                "year": w.get("publication_year"),
                "doi": w.get("doi"),
                "venue": venue,
                "cited_by": int(w.get("cited_by_count") or 0),
                "abstract": abstract_text[:600] if abstract_text else None,
                "openalex_id": w.get("id"),
                "is_open_access": (w.get("open_access") or {}).get("is_oa"),
            }
        )

    result = {
        "query": query,
        "total_results": total,
        "count_returned": len(papers),
        "papers": papers,
        "note": (
            f"OpenAlex hit {total:,} matching works; returned top {len(papers)} by citation count."
        ),
    }
    cache.put("search_openalex", cache_args, result)
    return result


def _abstract_from_inverted_index(inv: dict[str, list[int]]) -> str:
    """Reverse OpenAlex's inverted-index abstract back into a sentence.

    Each word maps to a sorted list of positions where it appears.
    Reconstruct the original word order, join with spaces.
    """
    if not inv:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))
