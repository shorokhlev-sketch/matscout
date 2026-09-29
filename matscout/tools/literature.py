"""Literature-search tools - Semantic Scholar + Crossref + arXiv.

Three free, no-auth APIs:

  - Semantic Scholar Graph API   (papers indexed across the web)
  - Crossref                     (canonical DOI metadata)
  - arXiv                        (preprints, Atom XML feed)

All three share our SQLite tool cache, so repeated lookups within a
session are free. None require an API key - that's deliberate; we don't
want to bake more secrets into the deploy.
"""

from __future__ import annotations

import time
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from matscout.models import Paper, PaperList
from matscout.tools._client import get_cache, polite_user_agent

# Crossref and Semantic Scholar treat named User-Agents better than
# anonymous ones. polite_user_agent() adds a contact address only when
# MATSCOUT_CONTACT_EMAIL is set.
# arXiv export endpoints can take 10-20s under load from EU; Crossref is
# usually <1s; Semantic Scholar varies. 30s gives the slow ones a chance
# without making the agent feel stuck.
_TIMEOUT = 30.0


def _http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int = 4,
) -> httpx.Response:
    """GET with retry on transient failures + follow redirects.

    Retries cover:
      - 429 (rate limit, Semantic Scholar especially)
      - ReadTimeout / ConnectTimeout (arXiv is occasionally slow)
      - 5xx responses (server-side hiccups)
    """
    delay = 1.5
    last: httpx.Response | None = None
    last_exc: Exception | None = None
    with httpx.Client(
        headers={"User-Agent": polite_user_agent()},
        timeout=_TIMEOUT,
        follow_redirects=True,
    ) as c:
        for _ in range(max_retries):
            try:
                last = c.get(url, params=params)
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ReadError) as e:
                last_exc = e
                time.sleep(delay)
                delay *= 2
                continue

            if last.status_code == 429 or last.status_code >= 500:
                ra = last.headers.get("retry-after")
                wait = float(ra) if (ra and ra.replace(".", "", 1).isdigit()) else delay
                time.sleep(min(wait, 8.0))
                delay *= 2
                continue
            return last

    if last is not None:
        return last
    # All attempts failed with network errors - synthesize a fake 504 response
    # so callers have a uniform shape to inspect.
    raise httpx.ReadTimeout(
        f"all {max_retries} retries timed out for {url}",
    ) from last_exc


# ---- Semantic Scholar ----
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_S2_FIELDS = (
    "paperId,title,abstract,year,authors.name,venue,citationCount,externalIds,openAccessPdf,url"
)


def _s2_to_paper(row: dict[str, Any]) -> Paper:
    ext = row.get("externalIds") or {}
    doi = ext.get("DOI") or ext.get("doi")
    return Paper(
        paper_id=f"s2:{row.get('paperId', '')}",
        source="semantic-scholar",
        title=str(row.get("title") or "").strip() or "(untitled)",
        authors=[(a or {}).get("name", "") for a in (row.get("authors") or []) if a],
        year=row.get("year"),
        venue=row.get("venue"),
        doi=doi,
        url=row.get("url") or (f"https://doi.org/{doi}" if doi else None),
        abstract=(row.get("abstract") or None),
        citation_count=row.get("citationCount"),
        open_access_url=(row.get("openAccessPdf") or {}).get("url"),
    )


def find_papers(
    query: str,
    *,
    year_from: int | None = None,
    limit: int = 10,
) -> PaperList:
    """NL search across Semantic Scholar's index.

    Args:
        query: free-text query, e.g. ``"cathode materials Na-ion battery"``.
        year_from: minimum publication year (inclusive). ``None`` = no bound.
        limit: max results (S2 caps at 100 per call).

    Returns: PaperList sorted by S2's own relevance score.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    cache_args = {"q": query.strip(), "year_from": year_from, "limit": limit, "src": "s2-search"}
    cache = get_cache()
    cached = cache.get("find_papers", cache_args)
    if cached is not None:
        return PaperList.model_validate(cached)

    params: dict[str, Any] = {
        "query": query,
        "limit": max(1, min(limit, 100)),
        "fields": _S2_FIELDS,
    }
    if year_from is not None:
        params["year"] = f"{year_from}-"

    r = _http_get(f"{_S2_BASE}/paper/search", params=params)
    if r.status_code in (403, 429):
        # Semantic Scholar's anonymous tier is aggressive: 429 for rate-limit,
        # 403 when a cloud IP is blanket-banned (we see this from VPS cidrs).
        # Either way, give the LLM an actionable instruction instead of crashing.
        reason = "rate-limited" if r.status_code == 429 else "blocked anonymous access"
        raise RuntimeError(
            f"Semantic Scholar {reason} (HTTP {r.status_code}). "
            "Fall back to `find_preprints` (arXiv has no such limit) "
            "or `get_doi_metadata` if you have a DOI."
        )
    r.raise_for_status()
    body = r.json()
    papers = [_s2_to_paper(row) for row in body.get("data", [])]
    result = PaperList(query=query, source="semantic-scholar", count=len(papers), papers=papers)
    cache.put("find_papers", cache_args, result.model_dump(mode="json"))
    return result


def get_papers_about(
    material_id_or_formula: str,
    *,
    year_from: int | None = None,
    limit: int = 10,
) -> PaperList:
    """Find papers that mention a specific material - by mp-id or formula.

    We craft an S2 query that ORs the obvious terms (mp-id, formula); S2's
    relevance ranking does the heavy lifting from there.
    """
    seed = material_id_or_formula.strip()
    if not seed:
        raise ValueError("material_id_or_formula must not be empty")

    # We bias the query toward 'materials science' context so an mp-id /
    # formula doesn't accidentally hit medical or unrelated literature.
    query_parts = [seed]
    # Add a couple of disambiguating terms only for raw formulas to avoid
    # diluting precise mp-id queries.
    if not seed.lower().startswith(("mp-", "mvc-")):
        query_parts.append("crystal structure")
    query = " ".join(query_parts)

    return find_papers(query, year_from=year_from, limit=limit)


# ---- Crossref ----
_CROSSREF = "https://api.crossref.org/works"


def get_doi_metadata(doi: str) -> Paper:
    """Resolve a DOI to a Crossref record (authors, year, title, journal)."""
    doi = doi.strip().lstrip("doi:").strip()
    if not doi:
        raise ValueError("doi must not be empty")
    cache_args = {"doi": doi}
    cache = get_cache()
    cached = cache.get("get_doi_metadata", cache_args)
    if cached is not None:
        return Paper.model_validate(cached)

    r = _http_get(f"{_CROSSREF}/{doi}")
    r.raise_for_status()
    msg = r.json().get("message") or {}

    title = " ".join(msg.get("title") or []) or "(untitled)"
    authors: list[str] = []
    for a in msg.get("author") or []:
        parts = " ".join(p for p in (a.get("given"), a.get("family")) if p)
        if parts:
            authors.append(parts)
    year = None
    issued = (msg.get("issued") or {}).get("date-parts")
    if issued and issued[0]:
        year = issued[0][0]
    venue = (msg.get("container-title") or [None])[0]
    abstract_raw = msg.get("abstract") or None
    abstract = None
    if abstract_raw:
        # Crossref ships JATS XML in 'abstract' - strip tags.
        try:
            abstract = "".join(
                t for t in ET.fromstring(f"<root>{abstract_raw}</root>").itertext()
            ).strip()
        except ET.ParseError:
            abstract = abstract_raw

    paper = Paper(
        paper_id=f"doi:{doi}",
        source="crossref",
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        url=f"https://doi.org/{doi}",
        abstract=abstract,
        citation_count=msg.get("is-referenced-by-count"),
    )
    cache.put("get_doi_metadata", cache_args, paper.model_dump(mode="json"))
    return paper


# ---- arXiv ----
_ARXIV = "http://export.arxiv.org/api/query"
_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _parse_arxiv_feed(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", _ARXIV_NS):
        arxiv_id_url = (entry.findtext("atom:id", default="", namespaces=_ARXIV_NS) or "").strip()
        arxiv_id = arxiv_id_url.rsplit("/", 1)[-1] if arxiv_id_url else ""
        title = (entry.findtext("atom:title", default="", namespaces=_ARXIV_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=_ARXIV_NS) or "").strip()
        published = entry.findtext("atom:published", default="", namespaces=_ARXIV_NS) or ""
        year: int | None = None
        if published[:4].isdigit():
            year = int(published[:4])
        authors = [
            (a.findtext("atom:name", namespaces=_ARXIV_NS) or "").strip()
            for a in entry.findall("atom:author", _ARXIV_NS)
        ]
        doi = entry.findtext("arxiv:doi", default=None, namespaces=_ARXIV_NS)
        pdf_url: str | None = None
        for link in entry.findall("atom:link", _ARXIV_NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break
        papers.append(
            Paper(
                paper_id=f"arxiv:{arxiv_id}",
                source="arxiv",
                title=title or "(untitled)",
                authors=[a for a in authors if a],
                year=year,
                venue="arXiv",
                doi=doi,
                url=arxiv_id_url or None,
                abstract=summary or None,
                citation_count=None,
                open_access_url=pdf_url,
            )
        )
    return papers


def find_preprints(
    query: str,
    *,
    max_age_days: int | None = None,
    limit: int = 10,
) -> PaperList:
    """Search arXiv for preprints matching ``query``.

    Args:
        query: free-text query (arXiv-style; ``cat:cond-mat.*`` and other
            arXiv operators work, but pure NL is fine too).
        max_age_days: if set, drop entries older than this.
        limit: max results (arXiv caps generously; we set up to 50).
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    cache_args = {"q": query.strip(), "max_age": max_age_days, "limit": limit}
    cache = get_cache()
    cached = cache.get("find_preprints", cache_args)
    if cached is not None:
        return PaperList.model_validate(cached)

    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max(1, min(limit, 50)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    r = _http_get(_ARXIV, params=params)
    r.raise_for_status()
    papers = _parse_arxiv_feed(r.text)

    if max_age_days is not None:
        current_year = time.gmtime().tm_year
        # Coarse year filter - fine-grained date comparison would need parsing
        # the 'published' field, but year-level is good enough at PhD scope.
        cutoff_year = current_year - max(0, max_age_days // 365)
        papers = [p for p in papers if (p.year or 0) >= cutoff_year]

    result = PaperList(query=query, source="arxiv", count=len(papers), papers=papers)
    cache.put("find_preprints", cache_args, result.model_dump(mode="json"))
    return result
