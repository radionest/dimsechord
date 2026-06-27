import pytest

from dimsechord._cache import DicomCache
from dimsechord._exceptions import AssociationError, MoveToSelfError
from dimsechord._models import DicomNode
from dimsechord._pool import AssociationPool
from dimsechord._pull_engine import PullEngine
from dimsechord._scp import StorageSCP


@pytest.fixture
def engine(fake_pacs, free_port, tmp_path):
    scp_port = free_port()
    pool = AssociationPool(aets=["DESTPOOL"], per_aet_cap=1)
    scp = StorageSCP()
    scp.start(aets=pool.aets, port=scp_port)
    # The fake PACS must know where to route DESTPOOL (our SCP).
    fake_pacs.register_destination("DESTPOOL", "127.0.0.1", scp_port)

    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs = DicomNode(aet=fake_pacs.aet, host="127.0.0.1", port=fake_pacs.port)
    eng = PullEngine(
        pool=pool, scp=scp, cache=cache, pacs=pacs,
        cmove_timeout=60.0, arrival_timeout=30.0,
    )
    try:
        yield eng, cache
    finally:
        scp.stop()
        cache.shutdown()


@pytest.mark.timeout(90)
def test_iter_series_sync_for_dimse_consumer(engine, seeded_study) -> None:
    """The sync core path: consumed by a plain (non-async) caller, e.g. the DIMSE thread."""
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    received = list(eng.iter_series(study, series))
    assert {str(ds.SOPInstanceUID) for ds in received} == set(seeded_study[series])

    cache.flush_pending_writes()
    assert cache.series_cached(study, series)
    assert cache.get_series_from_memory(study, series) is not None


@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_stream_series_async_for_http(engine, seeded_study) -> None:
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    received = [ds async for ds in eng.stream_series(study, series)]
    assert {str(ds.SOPInstanceUID) for ds in received} == set(seeded_study[series])

    cache.flush_pending_writes()
    assert cache.series_cached(study, series)
    assert cache.get_series_from_memory(study, series) is not None


@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_second_call_is_memory_hit(engine, seeded_study) -> None:
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    _ = [ds async for ds in eng.stream_series(study, series)]
    assert cache.get_series_from_memory(study, series) is not None
    again = [ds async for ds in eng.stream_series(study, series)]
    assert len(again) == len(seeded_study[series])


@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_ensure_series_returns_cached(engine, seeded_study) -> None:
    eng, _ = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    cached = await eng.ensure_series(study, series)
    assert len(cached.instances) == len(seeded_study[series])


@pytest.mark.timeout(90)
def test_unrouted_destination_raises_move_to_self(engine) -> None:
    eng, _ = engine
    # A study UID the PACS has no instances for → C-MOVE completes with 0 sub-ops.
    with pytest.raises(MoveToSelfError):
        list(eng.iter_series("9.9.9.NONEXISTENT", "8.8.8"))


@pytest.mark.timeout(30)
def test_real_move_failure_raises_association_error(free_port, tmp_path) -> None:
    """A connection-refused failure surfaces as AssociationError, not MoveToSelfError."""
    dead_port = free_port()
    scp_port = free_port()
    pool = AssociationPool(aets=["FAILPOOL"], per_aet_cap=1)
    scp = StorageSCP()
    scp.start(aets=["FAILPOOL"], port=scp_port)
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs = DicomNode(aet="DEADPACS", host="127.0.0.1", port=dead_port)
    eng = PullEngine(
        pool=pool,
        scp=scp,
        cache=cache,
        pacs=pacs,
        cmove_timeout=5.0,
        arrival_timeout=5.0,
    )
    try:
        with pytest.raises(AssociationError):
            list(eng.iter_series("9.9.9.DEAD", "8.8.8.DEAD"))
    finally:
        scp.stop()
        cache.shutdown()
