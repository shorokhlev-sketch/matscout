"""Lazy MPRester singleton + injectable cache.

Tools call `get_client()` to reach the live API and `get_cache()` for
read-through caching. Both are easy to monkeypatch in tests — that's
the *only* reason this lives in its own module.
"""

from __future__ import annotations

from typing import Any, Protocol

from mp_api.client import MPRester

from matscout.cache import Cache
from matscout.config import get_settings


class _ClientLike(Protocol):
    """Minimal slice of MPRester we use — lets tests pass a mock."""

    materials: Any


_client: _ClientLike | None = None
_cache: Cache | None = None


def get_client() -> _ClientLike:
    """Returns a cached MPRester instance (created on first call)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = MPRester(settings.mp_api_key, mute_progress_bars=True)
    return _client


def set_client(client: _ClientLike | None) -> None:
    """Test hook — inject a mock, or `None` to reset the singleton."""
    global _client
    _client = client


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = Cache(
            db_path=settings.cache_db_path,
            ttl_seconds=settings.cache_ttl_days * 24 * 3600,
        )
    return _cache


def set_cache(cache: Cache | None) -> None:
    """Test hook — inject an in-memory / tmp-dir cache, or `None` to reset."""
    global _cache
    _cache = cache
