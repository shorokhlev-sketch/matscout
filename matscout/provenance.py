"""Snapshot metadata builder — the 'when, where, with what' of each run.

Embedded into every research snapshot at save time. Surfaces in the UI
footer of /r/{id} pages and in the BibTeX note.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from matscout import __version__


@lru_cache(maxsize=1)
def _git_short_sha() -> str:
    """Returns the deployed commit short-SHA, or 'unknown' on failure.

    Cached because we're calling out to git on every save otherwise.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=10", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


@lru_cache(maxsize=1)
def _mp_api_version() -> str:
    try:
        import mp_api

        return getattr(mp_api, "__version__", "unknown")
    except Exception:
        return "unknown"


@lru_cache(maxsize=1)
def _openai_sdk_version() -> str:
    try:
        import openai

        return getattr(openai, "__version__", "unknown")
    except Exception:
        return "unknown"


def build_metadata(*, model: str = "gpt-4o") -> dict[str, Any]:
    """Snapshot of build-time + run-time provenance.

    These values change per deploy (commit SHA) or per cold-start of the
    process (timestamps), but stay constant within a single agent run —
    safe to bake into the saved snapshot once at the end.
    """
    return {
        "matscout_version": __version__,
        "matscout_commit": _git_short_sha(),
        "mp_api_version": _mp_api_version(),
        "openai_sdk_version": _openai_sdk_version(),
        "model": model,
        "snapshot_built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mp_data_source": "Materials Project · next-gen.materialsproject.org",
        "mp_data_license": "CC-BY 4.0",
        "mp_canonical_citation": (
            "Jain et al. The Materials Project: A materials genome approach to "
            "accelerating materials innovation. APL Materials 1(1), 011002 (2013). "
            "DOI: 10.1063/1.4812323"
        ),
    }
