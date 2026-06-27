import pytest

from dimsechord._cache import DicomCache
from dimsechord._models import DicomNode
from dimsechord._pull_engine import PullEngine


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
