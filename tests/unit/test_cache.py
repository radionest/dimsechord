import dataclasses
import logging

import pytest
from pydicom import Dataset, dcmread

from dimsechord._cache import (
    _INSTANCE_OVERHEAD_BYTES,
    DicomCache,
    MemoryCachedSeries,
    _series_size_bytes,
)
from tests.factories import make_instance


@pytest.fixture
def cache(tmp_path):
    c = DicomCache(
        base_dir=tmp_path / "cache",
        index_path=tmp_path / "index.db",
        ttl_hours=24,
        max_size_gb=10.0,
    )
    yield c
    c.shutdown()


def test_put_and_get_memory(cache) -> None:
    inst = make_instance("ST", "SE", "I1")
    cache.put_series_to_memory("ST", "SE", {"I1": inst})
    got = cache.get_series_from_memory("ST", "SE")
    assert isinstance(got, MemoryCachedSeries)
    assert "I1" in got.instances


def test_write_instance_persists_and_indexes(cache, tmp_path) -> None:
    inst = make_instance("ST", "SE", "I1")
    cache.write_instance("ST", "SE", "I1", inst, source="pacs")

    path = tmp_path / "cache" / "ST" / "SE" / "I1.dcm"
    assert path.exists()
    row = cache._index.get_instance("I1")
    assert row is not None and row.size > 0
    assert str(dcmread(path).SOPInstanceUID) == "I1"

    # Rows alone are not servable — completeness marker required (issue #15).
    assert cache.load_series_from_disk("ST", "SE") is None
    cache.mark_series_complete("ST", "SE", 1)
    loaded = cache.load_series_from_disk("ST", "SE")
    assert loaded is not None and "I1" in loaded


def test_schedule_tee_runs_in_background_then_flush(cache, tmp_path) -> None:
    inst = make_instance("ST", "SE", "I2")
    cache.schedule_tee("ST", "SE", "I2", inst)
    cache.flush_pending_writes()
    assert (tmp_path / "cache" / "ST" / "SE" / "I2.dcm").exists()
    assert cache.series_cached("ST", "SE") is False  # tee'd but never marked complete
    cache.mark_series_complete("ST", "SE", 1)
    assert cache.series_cached("ST", "SE") is True


def test_read_instance_via_index(cache) -> None:
    inst = make_instance("ST", "SE", "I9")
    cache.write_instance("ST", "SE", "I9", inst)
    ds = cache.read_instance("ST", "SE", "I9")
    assert ds is not None and str(ds.SOPInstanceUID) == "I9"
    assert cache.read_instance("ST", "SE", "MISSING") is None


def test_evict_expired_removes_file_and_row(cache, tmp_path) -> None:
    inst = make_instance("ST", "SE", "I1")
    cache.write_instance("ST", "SE", "I1", inst)
    # Force the index row to look ancient (frozen dataclass → use replace()).
    row = cache._index.get_instance("I1")
    cache._index.upsert(dataclasses.replace(row, cached_at=0.0))
    removed = cache.evict_expired()
    assert removed == 1
    assert not (tmp_path / "cache" / "ST" / "SE" / "I1.dcm").exists()
    assert cache._index.get_instance("I1") is None


def test_evict_by_size(cache) -> None:
    for i in range(3):
        cache.write_instance("ST", "SE", f"I{i}", make_instance("ST", "SE", f"I{i}"))
    cache._max_size_bytes = 1  # force eviction of all but the most-recently accessed
    removed = cache.evict_by_size()
    assert removed >= 1


def test_eviction_clears_completeness_marker(cache) -> None:
    for i in ("I1", "I2"):
        cache.write_instance("ST", "SE", i, make_instance("ST", "SE", i))
    cache.mark_series_complete("ST", "SE", 2)
    assert cache.series_cached("ST", "SE") is True

    # Age one instance so TTL eviction removes it (pattern from test_evict_expired_*).
    row = cache._index.get_instance("I1")
    cache._index.upsert(dataclasses.replace(row, cached_at=0.0))
    removed = cache.evict_expired()
    assert removed == 1

    assert cache._index.series_expected_count("ST", "SE") is None  # marker dropped
    assert cache.series_cached("ST", "SE") is False
    assert cache.load_series_from_disk("ST", "SE") is None


def test_load_series_without_marker_returns_none(cache) -> None:
    """Issue #15 regression: aborted-stream tee leftovers must not be served."""
    for i in ("I1", "I2"):
        cache.write_instance("ST", "SE", i, make_instance("ST", "SE", i))
    assert cache.load_series_from_disk("ST", "SE") is None


def test_load_series_count_mismatch_returns_none(cache) -> None:
    cache.write_instance("ST", "SE", "I1", make_instance("ST", "SE", "I1"))
    cache.mark_series_complete("ST", "SE", 2)  # fewer rows than expected
    assert cache.load_series_from_disk("ST", "SE") is None

    cache.write_instance("ST", "SE", "I2", make_instance("ST", "SE", "I2"))
    cache.write_instance("ST", "SE", "I3", make_instance("ST", "SE", "I3"))
    assert cache.load_series_from_disk("ST", "SE") is None  # more rows than expected


def test_load_series_complete_returns_all(cache) -> None:
    for i in ("I1", "I2"):
        cache.write_instance("ST", "SE", i, make_instance("ST", "SE", i))
    cache.mark_series_complete("ST", "SE", 2)
    loaded = cache.load_series_from_disk("ST", "SE")
    assert loaded is not None and set(loaded) == {"I1", "I2"}


def test_load_series_unreadable_file_returns_none(cache, tmp_path) -> None:
    """A corrupt/missing file poisons the whole series read — never a subset."""
    for i in ("I1", "I2"):
        cache.write_instance("ST", "SE", i, make_instance("ST", "SE", i))
    cache.mark_series_complete("ST", "SE", 2)
    (tmp_path / "cache" / "ST" / "SE" / "I1.dcm").write_bytes(b"garbage")
    assert cache.load_series_from_disk("ST", "SE") is None


def test_series_size_bytes_sums_pixel_payloads_and_overhead() -> None:
    entry = MemoryCachedSeries(
        study_uid="ST",
        series_uid="SE",
        instances={
            "I1": make_instance("ST", "SE", "I1", rows=4, columns=4),  # 16 pixel bytes
            "I2": make_instance("ST", "SE", "I2", rows=8, columns=8),  # 64 pixel bytes
        },
        cached_at=0.0,
    )
    assert _series_size_bytes(entry) == 16 + 64 + 2 * _INSTANCE_OVERHEAD_BYTES


def test_series_size_bytes_counts_float_pixels_and_bare_instances() -> None:
    no_pixels = make_instance("ST", "SE", "I1")
    del no_pixels.PixelData
    float_pixels = make_instance("ST", "SE", "I2")
    del float_pixels.PixelData
    float_pixels.FloatPixelData = b"\x00" * 32
    entry = MemoryCachedSeries(
        study_uid="ST",
        series_uid="SE",
        instances={"I1": no_pixels, "I2": float_pixels},
        cached_at=0.0,
    )
    assert _series_size_bytes(entry) == 32 + 2 * _INSTANCE_OVERHEAD_BYTES


_MEMORY_BUDGET_BYTES = 200_000  # fits two 81,920-byte series, not three


@pytest.fixture
def byte_capped_cache(tmp_path):
    c = DicomCache(
        base_dir=tmp_path / "cache",
        index_path=tmp_path / "index.db",
        memory_max_size_gb=_MEMORY_BUDGET_BYTES / 1024**3,
    )
    yield c
    c.shutdown()


def _one_instance_series(series_uid: str) -> dict[str, Dataset]:
    """Estimated 81,920 bytes: 256x256 pixel bytes + one instance overhead."""
    sop_uid = f"{series_uid}-I1"
    return {sop_uid: make_instance("ST", series_uid, sop_uid, rows=256, columns=256)}


def test_memory_tier_evicts_lru_when_over_byte_budget(byte_capped_cache) -> None:
    for series in ("SA", "SB", "SC"):
        byte_capped_cache.put_series_to_memory("ST", series, _one_instance_series(series))
    assert byte_capped_cache.get_series_from_memory("ST", "SA") is None  # LRU evicted
    assert byte_capped_cache.get_series_from_memory("ST", "SB") is not None
    assert byte_capped_cache.get_series_from_memory("ST", "SC") is not None
    assert byte_capped_cache._memory_cache.currsize <= _MEMORY_BUDGET_BYTES


def test_memory_tier_get_refreshes_lru_recency(byte_capped_cache) -> None:
    byte_capped_cache.put_series_to_memory("ST", "SA", _one_instance_series("SA"))
    byte_capped_cache.put_series_to_memory("ST", "SB", _one_instance_series("SB"))
    assert byte_capped_cache.get_series_from_memory("ST", "SA") is not None  # refresh SA
    byte_capped_cache.put_series_to_memory("ST", "SC", _one_instance_series("SC"))
    assert byte_capped_cache.get_series_from_memory("ST", "SB") is None  # SB was LRU
    assert byte_capped_cache.get_series_from_memory("ST", "SA") is not None


def test_oversized_series_served_uncached_with_warning(byte_capped_cache, caplog) -> None:
    instances = {  # 4 x 81,920 = 327,680 bytes > budget
        f"I{i}": make_instance("ST", "SE", f"I{i}", rows=256, columns=256) for i in range(4)
    }
    with caplog.at_level(logging.WARNING, logger="dimsechord._cache"):
        entry = byte_capped_cache.put_series_to_memory("ST", "SE", instances)
    assert isinstance(entry, MemoryCachedSeries)
    assert set(entry.instances) == set(instances)
    assert byte_capped_cache.get_series_from_memory("ST", "SE") is None
    assert "exceeds the memory tier budget" in caplog.text
