"""Smoke tests for the Pydantic settings layer."""

from __future__ import annotations

import pytest

from matscout.config import Settings, get_settings


def test_settings_pulls_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MP_API_KEY", "test-mp-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("MATSCOUT_LOG_FORMAT", "json")
    monkeypatch.setenv("MATSCOUT_CACHE_TTL_DAYS", "7")

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.mp_api_key == "test-mp-key"
    assert s.openai_api_key == "test-openai-key"
    assert s.log_format == "json"
    assert s.cache_ttl_days == 7


def test_cache_paths_resolve_under_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cache_db_path.name == "matscout.db"
    assert s.cache_db_path.parent.name == "cache"


def test_singleton_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    # Reset singleton for this test
    import matscout.config as cfg

    cfg._settings = None
    a = get_settings()
    b = get_settings()
    assert a is b
