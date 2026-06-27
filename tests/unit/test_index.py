from dimsechord._index import CacheIndex, IndexedInstance


def _inst(sop: str, *, series: str = "S1", size: int = 100, cached: float = 1000.0,
          accessed: float = 1000.0, source: str = "pacs") -> IndexedInstance:
    return IndexedInstance(
        study_uid="ST1", series_uid=series, sop_uid=sop,
        file_path=f"/cache/ST1/{series}/{sop}.dcm", size=size,
        cached_at=cached, last_accessed=accessed, source=source,
    )


def test_upsert_and_get_instance() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1"))
    row = idx.get_instance("I1")
    assert row is not None and row.sop_uid == "I1" and row.size == 100


def test_upsert_is_idempotent_on_sop_uid() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1", size=100))
    idx.upsert(_inst("I1", size=250))
    assert idx.get_instance("I1").size == 250
    assert len(idx.get_series("ST1", "S1")) == 1


def test_get_series_and_series_cached() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1"))
    idx.upsert(_inst("I2"))
    rows = idx.get_series("ST1", "S1")
    assert {r.sop_uid for r in rows} == {"I1", "I2"}
    assert idx.series_cached("ST1", "S1") is True
    assert idx.series_cached("ST1", "NOPE") is False


def test_total_size() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1", size=100))
    idx.upsert(_inst("I2", size=50))
    assert idx.total_size() == 150


def test_expired_returns_old_rows() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("OLD", cached=0.0))
    idx.upsert(_inst("NEW", cached=10_000.0))
    expired = idx.expired(ttl_seconds=3600, now=10_000.0)
    assert {r.sop_uid for r in expired} == {"OLD"}


def test_touch_updates_last_accessed() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1", accessed=1.0))
    idx.touch("I1", now=999.0)
    assert idx.get_instance("I1").last_accessed == 999.0


def test_lru_over_size_evicts_oldest_accessed_first() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("OLD", size=100, accessed=1.0))
    idx.upsert(_inst("MID", size=100, accessed=2.0))
    idx.upsert(_inst("NEW", size=100, accessed=3.0))
    # cap = 150 bytes → must drop 150 bytes worth, oldest-accessed first
    victims = idx.lru_over_size(max_size_bytes=150)
    assert [v.sop_uid for v in victims] == ["OLD", "MID"]


def test_lru_under_cap_returns_empty() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1", size=100))
    assert idx.lru_over_size(max_size_bytes=1000) == []


def test_delete() -> None:
    idx = CacheIndex(":memory:")
    idx.upsert(_inst("I1"))
    idx.delete("I1")
    assert idx.get_instance("I1") is None
