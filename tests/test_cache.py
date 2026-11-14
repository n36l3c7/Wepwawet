from pathlib import Path

import pytest

from wepwawet.core.cache import TTLCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def cache(clock: FakeClock) -> TTLCache:
    return TTLCache.in_memory(clock)


def test_round_trip(cache: TTLCache) -> None:
    cache.set("rdap", "198.51.100.23", {"country": "IT", "netname": "EXAMPLE"}, ttl_seconds=60)
    assert cache.get("rdap", "198.51.100.23") == {"country": "IT", "netname": "EXAMPLE"}


def test_missing_key(cache: TTLCache) -> None:
    assert cache.get("rdap", "nothing") is None


def test_sources_are_separate(cache: TTLCache) -> None:
    cache.set("rdap", "k", 1, ttl_seconds=60)
    cache.set("ripestat", "k", 2, ttl_seconds=60)
    assert (cache.get("rdap", "k"), cache.get("ripestat", "k")) == (1, 2)


def test_entry_expires(cache: TTLCache, clock: FakeClock) -> None:
    cache.set("rdap", "k", "v", ttl_seconds=10)
    clock.now += 9.9
    assert cache.get("rdap", "k") == "v"
    clock.now += 0.1
    assert cache.get("rdap", "k") is None


def test_set_overwrites_and_refreshes_ttl(cache: TTLCache, clock: FakeClock) -> None:
    cache.set("rdap", "k", "old", ttl_seconds=10)
    clock.now += 8
    cache.set("rdap", "k", "new", ttl_seconds=10)
    clock.now += 8
    assert cache.get("rdap", "k") == "new"


def test_purge_expired(cache: TTLCache, clock: FakeClock) -> None:
    cache.set("a", "1", 1, ttl_seconds=5)
    cache.set("a", "2", 2, ttl_seconds=50)
    clock.now += 10
    assert cache.purge_expired() == 1
    assert cache.get("a", "2") == 2


def test_clear_one_source(cache: TTLCache) -> None:
    cache.set("a", "1", 1, ttl_seconds=5)
    cache.set("b", "1", 1, ttl_seconds=5)
    assert cache.clear("a") == 1
    assert cache.get("b", "1") == 1


def test_rejects_non_positive_ttl(cache: TTLCache) -> None:
    with pytest.raises(ValueError, match="positive"):
        cache.set("a", "1", 1, ttl_seconds=0)


def test_persists_on_disk(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "nested" / "cache.sqlite3"
    TTLCache.open(path, clock).set("a", "1", [1, 2], ttl_seconds=60)
    assert TTLCache.open(path, clock).get("a", "1") == [1, 2]
