import pydicom.uid
import pytest

from dimsechord._cache import DicomCache, MemoryCachedSeries
from dimsechord._exceptions import AssociationError
from dimsechord._models import DicomNode, RetrieveResult
from dimsechord._pull_engine import PullEngine
from dimsechord._scu import DicomOperations
from tests.factories import make_instance


@pytest.fixture
def cget_engine(fake_pacs, tmp_path):
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs = DicomNode(aet=fake_pacs.aet, host="127.0.0.1", port=fake_pacs.port)
    eng = PullEngine.via_cget(cache=cache, pacs=pacs, calling_aet="GETSCU")
    try:
        yield eng, cache
    finally:
        cache.shutdown()


@pytest.mark.timeout(60)
def test_via_cget_iter_series_fills_cache(cget_engine, seeded_study) -> None:
    eng, cache = cget_engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    received = list(eng.iter_series(study, series))
    assert {str(ds.SOPInstanceUID) for ds in received} == set(seeded_study[series])

    cache.flush_pending_writes()
    assert cache.series_cached(study, series)
    assert cache.get_series_from_memory(study, series) is not None


@pytest.mark.timeout(60)
def test_via_cget_second_call_is_memory_hit(cget_engine, seeded_study) -> None:
    eng, cache = cget_engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    _ = list(eng.iter_series(study, series))
    assert cache.get_series_from_memory(study, series) is not None
    again = list(eng.iter_series(study, series))
    assert len(again) == len(seeded_study[series])


@pytest.mark.timeout(60)
async def test_via_cget_ensure_series(cget_engine, seeded_study) -> None:
    eng, _ = cget_engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    cached = await eng.ensure_series(study, series)
    assert len(cached.instances) == len(seeded_study[series])


@pytest.mark.timeout(60)
def test_empty_retrieve_does_not_poison_cache_iter(cget_engine, seeded_study) -> None:
    eng, cache = cget_engine
    study = seeded_study["study"][0]
    missing_series = pydicom.uid.generate_uid()

    result = list(eng.iter_series(study, missing_series))
    assert result == []

    cache.flush_pending_writes()
    assert cache.get_series_from_memory(study, missing_series) is None


@pytest.mark.timeout(60)
async def test_empty_retrieve_does_not_poison_cache_ensure(cget_engine, seeded_study) -> None:
    eng, cache = cget_engine
    study = seeded_study["study"][0]
    missing_series = pydicom.uid.generate_uid()

    cached = await eng.ensure_series(study, missing_series)

    assert isinstance(cached, MemoryCachedSeries)
    assert cached.instances == {}
    assert cache.get_series_from_memory(study, missing_series) is None


@pytest.mark.timeout(60)
def test_partial_cget_does_not_poison_cache(
    cget_engine, seeded_study, monkeypatch
) -> None:
    """A partially-failed C-GET must raise AssociationError and leave the cache clean."""
    eng, cache = cget_engine
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    sop = seeded_study[series][0]
    partial_instance = make_instance(study, series, sop)

    def _stubbed_retrieve_via_get(_self, _config, _request, _storage, _on_progress=None):
        return RetrieveResult(
            status="warning_0xb000",
            num_completed=1,
            num_failed=1,
            instances={sop: partial_instance},
        )

    monkeypatch.setattr(DicomOperations, "retrieve_via_get", _stubbed_retrieve_via_get)

    with pytest.raises(AssociationError, match="C-GET incomplete"):
        list(eng.iter_series(study, series))

    cache.flush_pending_writes()
    assert cache.get_series_from_memory(study, series) is None, "memory cache must be clean"
    assert cache.series_cached(study, series) is False, "disk cache must be clean"
