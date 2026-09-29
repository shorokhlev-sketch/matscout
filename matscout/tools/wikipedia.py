"""Wikipedia REST - short context lookups for materials, elements, methods.

Used when the agent needs a one-paragraph plain-language definition
or some pop-science context that the DFT databases don't carry. For
example: "what is the Shockley-Queisser limit", "what is a Wadsley-
Roth phase", "history of silicon photovoltaics".

This is a complement, not a primary research source. The agent should
quote Wikipedia sparingly - for textbook context and definitions, not
for numerical claims (use MP / JARVIS / arXiv for those).
"""

from __future__ import annotations

from typing import Any

import httpx

from matscout.tools._client import get_cache

_USER_AGENT = "matscout/0.1 (+https://matscout.prfo.design)"
_HTTP_TIMEOUT = 10.0


def get_wikipedia_summary(
    title: str,
    *,
    lang: str = "en",
) -> dict[str, Any]:
    """One-paragraph summary of a Wikipedia article.

    Args:
        title: article title or natural-language redirect target,
            e.g. ``"Shockley-Queisser limit"`` or ``"Silicon"``.
        lang: language code (``"en"``, ``"ru"``, ...). Defaults to en.

    Returns:
        ``{
            "title", "lang", "extract", "url", "available": bool,
            "note": str
        }``
    """
    cache_args = {"title": title, "lang": lang}
    cache = get_cache()
    cached = cache.get("get_wikipedia_summary", cache_args)
    if cached is not None:
        return cached

    # Wikipedia's REST summary endpoint - short, ~3-sentence lead.
    safe = httpx.URL(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}")
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as c:
            resp = c.get(str(safe), headers={"User-Agent": _USER_AGENT})
    except (httpx.HTTPError, OSError) as e:
        return {
            "title": title,
            "lang": lang,
            "extract": None,
            "url": None,
            "available": False,
            "note": f"Wikipedia unreachable: {type(e).__name__}: {e}",
        }
    if resp.status_code == 404:
        return {
            "title": title,
            "lang": lang,
            "extract": None,
            "url": None,
            "available": False,
            "note": f"No Wikipedia article titled {title!r} in {lang}.",
        }
    if resp.status_code != 200:
        return {
            "title": title,
            "lang": lang,
            "extract": None,
            "url": None,
            "available": False,
            "note": f"Wikipedia returned HTTP {resp.status_code}",
        }

    body = resp.json()
    result = {
        "title": body.get("title", title),
        "lang": lang,
        "extract": body.get("extract"),
        "url": (body.get("content_urls") or {}).get("desktop", {}).get("page"),
        "available": True,
        "note": (
            "One-paragraph lead from Wikipedia. For textbook definitions and "
            "historical context only. Do NOT quote numerical claims from here "
            "without cross-checking against a primary source (MP, JARVIS, "
            "Crossref, arXiv)."
        ),
    }
    cache.put("get_wikipedia_summary", cache_args, result)
    return result
