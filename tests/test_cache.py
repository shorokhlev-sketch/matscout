"""Cache invariants - hit/miss, TTL, key stability, observability."""

from __future__ import annotations

from pathlib import Path

import pytest

from matscout.cache import Cache, _canonical_args, _hash


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    return Cache(db_path=tmp_path / "test.db", ttl_seconds=3600)


# ---- key stability ----
def test_canonical_args_order_independent() -> None:
    a = _canonical_args({"x": 1, "y": [3, 2, 1]})
    b = _canonical_args({"y": [3, 2, 1], "x": 1})
    assert a == b


def test_hash_stable_across_arg_order() -> None:
    assert _hash("search", {"x": 1, "y": 2}) == _hash("search", {"y": 2, "x": 1})


def test_hash_changes_with_tool_name() -> None:
    assert _hash("search", {"x": 1}) != _hash("get", {"x": 1})


def test_hash_changes_with_arg_value() -> None:
    assert _hash("search", {"x": 1}) != _hash("search", {"x": 2})


# ---- miss / put / get ----
def test_miss_returns_none(cache: Cache) -> None:
    assert cache.get("search", {"q": "x"}) is None


def test_put_then_get_roundtrips(cache: Cache) -> None:
    payload = {"hits": [1, 2, 3], "meta": {"chunk": 1}}
    cache.put("search", {"q": "x"}, payload)
    got = cache.get("search", {"q": "x"})
    assert got == payload


def test_put_overwrites_and_resets_hit_count(cache: Cache) -> None:
    cache.put("search", {"q": "x"}, {"v": 1})
    cache.get("search", {"q": "x"})  # bump hit_count → 1
    cache.put("search", {"q": "x"}, {"v": 2})  # overwrite
    stats = cache.stats()
    assert stats["by_tool"]["search"]["hits"] == 0


# ---- TTL ----
def test_ttl_expiry(tmp_path: Path) -> None:
    now = [1_000_000]
    c = Cache(db_path=tmp_path / "ttl.db", ttl_seconds=60, clock=lambda: now[0])
    c.put("search", {"q": "x"}, {"v": 1})
    now[0] += 30
    assert c.get("search", {"q": "x"}) == {"v": 1}  # still fresh
    now[0] += 31  # total 61s → expired
    assert c.get("search", {"q": "x"}) is None


def test_vacuum_removes_expired(tmp_path: Path) -> None:
    now = [1_000_000]
    c = Cache(db_path=tmp_path / "vac.db", ttl_seconds=10, clock=lambda: now[0])
    c.put("search", {"q": "a"}, {"v": 1})
    now[0] += 5
    c.put("search", {"q": "b"}, {"v": 2})
    now[0] += 7  # entry "a" is at 12s (expired), "b" at 7s (fresh)
    removed = c.vacuum()
    assert removed == 1
    assert c.get("search", {"q": "a"}) is None
    assert c.get("search", {"q": "b"}) == {"v": 2}


# ---- stats / clear ----
def test_stats_increment_hit_count(cache: Cache) -> None:
    cache.put("search", {"q": "x"}, {"v": 1})
    cache.get("search", {"q": "x"})
    cache.get("search", {"q": "x"})
    cache.get("search", {"q": "x"})
    stats = cache.stats()
    assert stats["total_entries"] == 1
    assert stats["total_hits"] == 3
    assert stats["by_tool"]["search"] == {"entries": 1, "hits": 3}


def test_clear_all(cache: Cache) -> None:
    cache.put("search", {"q": "a"}, {"v": 1})
    cache.put("get", {"id": "mp-1"}, {"v": 2})
    removed = cache.clear()
    assert removed == 2
    assert cache.stats()["total_entries"] == 0


def test_clear_per_tool(cache: Cache) -> None:
    cache.put("search", {"q": "a"}, {"v": 1})
    cache.put("get", {"id": "mp-1"}, {"v": 2})
    removed = cache.clear(tool_name="search")
    assert removed == 1
    stats = cache.stats()
    assert "search" not in stats["by_tool"]
    assert "get" in stats["by_tool"]


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    a = Cache(db_path=path)
    a.put("search", {"q": "x"}, {"v": "hello"})
    b = Cache(db_path=path)
    assert b.get("search", {"q": "x"}) == {"v": "hello"}
