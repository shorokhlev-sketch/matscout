"""Snapshot metadata builder — the 'when, where, with what' of each run.

Embedded into every research snapshot at save time. Surfaces in the UI
footer of /r/{id} pages and in the BibTeX note.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from matscout import __version__

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
# A baked file gets the commit SHA from the deploy step (or git checkout)
# so production doesn't need git installed / a working directory inside .git.
_VERSION_FILE = _PROJECT_ROOT / "VERSION"


@lru_cache(maxsize=1)
def _git_short_sha() -> str:
    """Returns the deployed commit short-SHA, or 'unknown' on failure.

    Try order:
      1. ``MATSCOUT_COMMIT`` env var (set explicitly in systemd unit)
      2. ``VERSION`` file at the project root (baked by CI / deploy script)
      3. ``git rev-parse`` from inside the source tree
    """
    if env := os.environ.get("MATSCOUT_COMMIT"):
        return env.strip()[:10] or "unknown"
    if _VERSION_FILE.exists():
        try:
            v = _VERSION_FILE.read_text(encoding="utf-8").strip()
            if v:
                return v[:10]
        except OSError:
            pass
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=10", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=_PROJECT_ROOT,
            timeout=2,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


def _pkg_version(name: str) -> str:
    """Resolve an installed package's version via the metadata registry.

    Works regardless of whether the package itself exposes ``__version__``.
    """
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=1)
def _mp_api_version() -> str:
    return _pkg_version("mp-api")


@lru_cache(maxsize=1)
def _openai_sdk_version() -> str:
    return _pkg_version("openai")


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
