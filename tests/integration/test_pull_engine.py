import threading
import time

import pytest
from pydicom.uid import JPEGLSLossless, generate_uid

from dimsechord._cache import DicomCache
from dimsechord._exceptions import AssociationError, MoveToSelfError, PoolExhaustedError
from dimsechord._models import DicomNode, RetrieveResult
from dimsechord._pool import AssociationPool
from dimsechord._pull_engine import PullEngine
from dimsechord._scp import MoveSession, StorageSCP
from dimsechord._scu import DicomOperations
from tests.factories import make_compressed_instance, make_instance


@pytest.fixture
def engine(fake_pacs, free_port, tmp_path):
    scp_port = free_port()
    pool = AssociationPool(aets=["DESTPOOL"], per_aet_cap=1)
    scp = StorageSCP()
    scp.start(dict.fromkeys(pool.aets, scp_port))
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
    scp.start({"FAILPOOL": scp_port})
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


@pytest.mark.timeout(90)
def test_move_under_delivery_raises_association_error(monkeypatch, engine, seeded_study) -> None:
    """Issue #15 blocker: failed C-MOVE sub-operations must not be cached as complete.

    Mirrors the C-GET transport guard: move() returning num_failed > 0
    must end the stream with AssociationError, not a clean finish.
    """
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    def fake_move(self, config, request, destination_aet, *, abort_handle=None):  # noqa: ARG001
        return RetrieveResult(status="success", num_completed=1, num_failed=1)

    monkeypatch.setattr(DicomOperations, "move", fake_move)

    with pytest.raises(AssociationError):
        list(eng.iter_series(study, series))

    assert cache._index.series_expected_count(study, series) is None
    assert cache.series_cached(study, series) is False


@pytest.mark.timeout(90)
def test_move_nonsuccess_status_not_marked_complete(monkeypatch, engine, seeded_study) -> None:
    """Issue #15 blocker: a non-success terminal C-MOVE status must not be cached.

    A peer that aborts/refuses after k of N deliveries leaves the response stream
    ending on a still-``pending`` (or warning) status with num_failed unset. That
    must raise, not finish the stream cleanly and mark the partial series complete.
    """
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    def fake_move(self, config, request, destination_aet, *, abort_handle=None):  # noqa: ARG001
        return RetrieveResult(status="pending", num_completed=1, num_failed=0)

    monkeypatch.setattr(DicomOperations, "move", fake_move)

    with pytest.raises(AssociationError):
        list(eng.iter_series(study, series))

    assert cache._index.series_expected_count(study, series) is None
    assert cache.series_cached(study, series) is False


@pytest.mark.timeout(30)
def test_move_arrival_shortfall_not_marked_complete(monkeypatch, free_port, tmp_path) -> None:
    """Issue #15 blocker: status success but the instances never physically arrive.

    move reports 2 completed sub-ops, yet no C-STORE reaches the SCP within
    the completion grace. The shortfall must raise, not finish cleanly and certify
    a short series. Uses a dedicated engine with a small grace to stay fast.
    """
    scp_port = free_port()
    pool = AssociationPool(aets=["SHORTPOOL"], per_aet_cap=1)
    scp = StorageSCP()
    scp.start({"SHORTPOOL": scp_port})
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs = DicomNode(aet="PACS", host="127.0.0.1", port=free_port())
    eng = PullEngine(
        pool=pool, scp=scp, cache=cache, pacs=pacs,
        cmove_timeout=5.0, arrival_timeout=5.0, completion_grace=0.5,
    )

    def fake_move(self, config, request, destination_aet, *, abort_handle=None):  # noqa: ARG001
        return RetrieveResult(status="success", num_completed=2, num_failed=0)

    monkeypatch.setattr(DicomOperations, "move", fake_move)

    study, series = "9.9.9.SHORT", "8.8.8.SHORT"
    try:
        with pytest.raises(AssociationError):
            list(eng.iter_series(study, series))
        assert cache._index.series_expected_count(study, series) is None
        assert cache.series_cached(study, series) is False
    finally:
        scp.stop()
        cache.shutdown()


@pytest.mark.timeout(90)
def test_full_pull_marks_series_complete(engine, seeded_study) -> None:
    """A cleanly exhausted stream records the series as complete on disk."""
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    received = list(eng.iter_series(study, series))
    assert len(received) == len(seeded_study[series])
    # Marker is written synchronously at stream end — no flush needed.
    assert cache._index.series_expected_count(study, series) == len(seeded_study[series])


@pytest.mark.timeout(90)
def test_study_pull_marks_each_series_complete(engine, seeded_study) -> None:
    eng, cache = engine
    study = seeded_study["study"][0]
    s1, s2 = seeded_study["series"]

    received = list(eng.iter_study(study, [s1, s2]))
    assert len(received) == len(seeded_study[s1]) + len(seeded_study[s2])
    assert cache._index.series_expected_count(study, s1) == len(seeded_study[s1])
    assert cache._index.series_expected_count(study, s2) == len(seeded_study[s2])


@pytest.mark.timeout(90)
def test_aborted_stream_not_served_from_disk(
    monkeypatch, engine, seeded_study, fake_pacs
) -> None:
    """Issue #15 e2e: a consumer abandoning the stream must not poison the disk tier.

    Shrinks the move AE's dimse_timeout (same test-only seam as
    test_scu_move.py's cross-thread-abort test): abort cannot wake a driver
    already parked in the DIMSE receive, so this bounds how long the reaper
    takes to release the slot the re-pull below needs, instead of the real
    ~30s dimse_timeout default.
    """
    original_create_ae = DicomOperations._create_ae

    def create_ae_with_short_dimse(self):
        ae = original_create_ae(self)
        ae.dimse_timeout = 2.0  # abort can't wake a parked DIMSE receive; bound it for the test
        return ae

    monkeypatch.setattr(DicomOperations, "_create_ae", create_ae_with_short_dimse)

    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    gen = eng.iter_series(study, series)
    next(gen)
    gen.close()  # early disconnect after 1 of 2 instances

    cache.flush_pending_writes()
    assert len(cache._index.get_series(study, series)) == 1  # partial tee leftover
    assert cache.series_cached(study, series) is False
    assert cache.load_series_from_disk(study, series) is None
    assert cache.get_series_from_memory(study, series) is None

    moves_before = len(fake_pacs.moves)
    received = list(eng.iter_series(study, series))
    assert len(fake_pacs.moves) == moves_before + 1  # re-pulled, not served from disk
    assert {str(ds.SOPInstanceUID) for ds in received} == set(seeded_study[series])

    cache.flush_pending_writes()
    assert cache.series_cached(study, series) is True


@pytest.mark.timeout(90)
def test_complete_series_served_from_disk_without_transport(
    engine, seeded_study, fake_pacs
) -> None:
    eng, cache = engine
    study, series = seeded_study["study"][0], seeded_study["series"][0]

    _ = list(eng.iter_series(study, series))
    cache.flush_pending_writes()
    cache._memory_cache.clear()  # expire the memory tier; disk must serve alone

    moves_before = len(fake_pacs.moves)
    received = list(eng.iter_series(study, series))
    assert {str(ds.SOPInstanceUID) for ds in received} == set(seeded_study[series])
    assert len(fake_pacs.moves) == moves_before  # no new C-MOVE
    promoted = cache.get_series_from_memory(study, series)
    assert promoted is not None and promoted.disk_persisted is True


def _await_session(scp: StorageSCP, key: str, timeout: float = 5.0) -> MoveSession:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with scp._lock:
            session = scp._sessions.get(key)
        if session is not None:
            return session
        time.sleep(0.005)
    raise AssertionError(f"session {key} was not registered within {timeout}s")


@pytest.mark.timeout(30)
def test_orphaned_move_driver_not_certified_complete(monkeypatch, free_port, tmp_path) -> None:
    """Issue #15 blocker: a driver still alive after the bounded abort-join must raise,
    and its move slot must stay held until the driver actually exits.

    When the stream breaks on the end-of-stream sentinel (shutdown mid-pull) while the
    C-MOVE driver thread is still stuck inside ``move``, abort cannot wake a driver
    already parked in the DIMSE receive (measured: it only exits at the AE's
    dimse_timeout), so the bounded ``move_thread.join(_ABORT_JOIN_TIMEOUT)`` returns
    with the thread alive, ``move_error`` empty and a partial set delivered. That must
    raise ``AssociationError`` rather than finish cleanly and certify the partial
    delivery as a complete series — and the pool must not free the slot until the
    reaper observes the driver exit.
    """
    scp_port = free_port()
    pool = AssociationPool(aets=["ORPHANPOOL"], per_aet_cap=1)
    scp = StorageSCP()
    scp.start({"ORPHANPOOL": scp_port})
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs = DicomNode(aet="PACS", host="127.0.0.1", port=free_port())
    eng = PullEngine(
        pool=pool, scp=scp, cache=cache, pacs=pacs,
        cmove_timeout=0.5, arrival_timeout=30.0,
    )

    release = threading.Event()

    def blocking_move(self, config, request, destination_aet, *, abort_handle=None):  # noqa: ARG001
        release.wait(timeout=20)  # driver hangs here → thread stays alive
        return RetrieveResult(status="success", num_completed=1, num_failed=0)

    monkeypatch.setattr(DicomOperations, "move", blocking_move)

    study, series = "9.9.9.ORPHAN", "8.8.8.ORPHAN"
    scp_key = f"{study}/{series}"
    result: dict[str, object] = {}

    def consume() -> None:
        try:
            result["received"] = list(eng.iter_series(study, series))
        except Exception as e:
            result["error"] = e

    consumer = threading.Thread(target=consume, name="orphan-consumer")
    try:
        consumer.start()
        session = _await_session(scp, scp_key)
        # Deliver one instance, then end the stream while the driver is still hung.
        inst = make_instance(study, series, "1.2.3.ORPHAN.1")
        session.queue.put(("1.2.3.ORPHAN.1", inst))
        scp.signal_end(scp_key)

        consumer.join(timeout=10)
        assert not consumer.is_alive()
        assert isinstance(result.get("error"), AssociationError)
        # The slot must NOT have been returned while the driver is alive…
        with pytest.raises(PoolExhaustedError):
            pool._acquire_move(timeout=0.1)
        # …and must return via the reaper once the driver exits.
        release.set()
        deadline = time.monotonic() + 5.0
        reacquired = None
        while time.monotonic() < deadline:
            try:
                reacquired = pool._acquire_move(timeout=0.2)
                break
            except PoolExhaustedError:
                continue
        assert reacquired is not None
        reacquired.release()
        assert cache._index.series_expected_count(study, series) is None
        assert cache.series_cached(study, series) is False
    finally:
        release.set()  # let the orphaned driver thread finish and die
        scp.stop()
        cache.shutdown()


@pytest.mark.timeout(90)
def test_move_to_self_compressed_series_verbatim(engine, fake_pacs, seeded_study) -> None:
    """A compressed series survives C-MOVE-to-self byte-identical (no transcoding)."""
    eng, _ = engine
    study = seeded_study["study"][0]
    series = generate_uid()
    sops = [generate_uid(), generate_uid()]
    for sop in sops:
        fake_pacs.add_instance(make_compressed_instance(study, series, sop))

    received = list(eng.iter_series(study, series))

    assert {str(ds.SOPInstanceUID) for ds in received} == set(sops)
    assert all(ds.file_meta.TransferSyntaxUID == JPEGLSLossless for ds in received)
