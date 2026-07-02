import dataclasses

import pytest
from pydicom import dcmread

from dimsechord._cache import DicomCache, MemoryCachedSeries
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
    assert cache.series_cached("ST", "SE")


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
