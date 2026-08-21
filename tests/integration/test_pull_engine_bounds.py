"""Repro-style bounds tests for the retrieve path (incident: bounded-retrieve-path)."""

import threading
import time
import warnings

import pytest

from dimsechord._cache import DicomCache
from dimsechord._exceptions import PoolExhaustedError, RetrieveBusyError
from dimsechord._models import DicomNode
from dimsechord._pool import AssociationPool
from dimsechord._pull_engine import PullEngine
from dimsechord._scp import StorageSCP


def test_cget_engine_coalescing_bound(tmp_path) -> None:
    """The busy bound is transport-independent: a held per-key lock on a C-GET
    engine raises RetrieveBusyError within move_lease_timeout."""
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    eng = PullEngine.via_cget(
        cache, DicomNode(aet="P", host="127.0.0.1", port=104),
        calling_aet="GETTER", coalesce_timeout=0.2,
    )
    lock = eng._get_lock("S/X")  # the exact key iter_series("S", "X") computes
    assert lock.acquire(timeout=1)
    try:
        start = time.monotonic()
        with pytest.raises(RetrieveBusyError):
            list(eng.iter_series("S", "X"))
        assert time.monotonic() - start < 1.0
    finally:
        lock.release()
        cache.shutdown()


def test_cmove_timeout_override_warns_deprecated(tmp_path) -> None:
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pool = AssociationPool(aets=["DEPWARN"])
    scp = StorageSCP()
    pacs = DicomNode(aet="PACS", host="127.0.0.1", port=104)
    try:
        with pytest.warns(DeprecationWarning, match="cmove_timeout"):
            PullEngine(pool=pool, scp=scp, cache=cache, pacs=pacs, cmove_timeout=60.0)
    finally:
        cache.shutdown()


def test_cmove_timeout_default_does_not_warn(tmp_path) -> None:
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pool = AssociationPool(aets=["DEPWARN2"])
    scp = StorageSCP()
    pacs = DicomNode(aet="PACS", host="127.0.0.1", port=104)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            PullEngine(pool=pool, scp=scp, cache=cache, pacs=pacs)
    finally:
        cache.shutdown()


def _make_engine(fake_pacs, free_port, tmp_path, **kwargs):
    scp_port = free_port()
    pool = AssociationPool(aets=["BOUNDPOOL"], per_aet_cap=1)
    scp = StorageSCP()
    scp.start({"BOUNDPOOL": scp_port})
    fake_pacs.register_destination("BOUNDPOOL", "127.0.0.1", scp_port)
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs = DicomNode(aet=fake_pacs.aet, host="127.0.0.1", port=fake_pacs.port)
    eng = PullEngine(pool=pool, scp=scp, cache=cache, pacs=pacs, **kwargs)
    return eng, pool, scp, cache


@pytest.mark.timeout(30)
def test_all_slots_busy_fails_fast_without_upstream_move(
    fake_pacs, seeded_study, free_port, tmp_path
) -> None:
    eng, _pool, scp, cache = _make_engine(
        fake_pacs, free_port, tmp_path, arrival_timeout=30.0, move_lease_timeout=0.3
    )
    study = seeded_study["study"][0]
    s1, s2 = seeded_study["series"]
    fake_pacs.move_response_delay = 1.0  # the first move is slow, holding the only slot
    gen = eng.iter_series(study, s1)
    try:
        next(gen)  # slot leased, move in flight
        moves_before = len(fake_pacs.move_identifiers)
        start = time.monotonic()
        with pytest.raises(PoolExhaustedError):
            list(eng.iter_series(study, s2))  # different series → pool, not coalescing
        assert time.monotonic() - start < 1.0
        assert len(fake_pacs.move_identifiers) == moves_before  # no upstream C-MOVE
        with scp._lock:
            assert f"{study}/{s2}" not in scp._sessions  # no session was registered
    finally:
        gen.close()
        scp.stop()
        cache.shutdown()


@pytest.mark.timeout(60)
def test_abandoned_move_frees_slot_and_kills_driver(
    fake_pacs, seeded_study, free_port, tmp_path, caplog, monkeypatch
) -> None:
    import logging

    from dimsechord._scu import DicomOperations

    original_create_ae = DicomOperations._create_ae

    def create_ae_with_short_dimse(self):
        ae = original_create_ae(self)
        ae.dimse_timeout = 2.0  # abort can't wake a parked DIMSE receive; bound it
        return ae

    monkeypatch.setattr(DicomOperations, "_create_ae", create_ae_with_short_dimse)

    eng, _pool, scp, cache = _make_engine(
        fake_pacs, free_port, tmp_path, arrival_timeout=10.0, move_lease_timeout=0.5
    )
    study = seeded_study["study"][0]
    s1, s2 = seeded_study["series"]
    # Matches both the driver ("dimsechord-move-{scp_key}") and reaper
    # ("dimsechord-move-reaper-{scp_key}") thread names for THIS move only —
    # a bare "dimsechord-move-" prefix also matches other tests' orphaned
    # driver/reaper threads (e.g. repro (a) intentionally leaves one alive
    # for the real ~30s dimse_timeout default, since it never shrinks it),
    # which would make this check flaky depending on suite run order.
    scp_key = f"{study}/{s1}"
    fake_pacs.move_response_delay = 0.7
    with caplog.at_level(logging.WARNING):
        gen = eng.iter_series(study, s1)
        next(gen)  # 1 of 2 yielded; upstream sleeping before instance 2
        start = time.monotonic()
        gen.close()  # GeneratorExit → abort upstream → 2 s bounded join → reaper handoff
        assert time.monotonic() - start < 4.0  # consumer never waits past the join

        # Driver exits at the shrunk DIMSE timeout (~2 s after its receive began);
        # the reaper then releases the slot and both threads disappear.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and any(
            t.name.startswith("dimsechord-move-") and scp_key in t.name
            for t in threading.enumerate()
        ):
            time.sleep(0.05)
        assert not any(
            t.name.startswith("dimsechord-move-") and scp_key in t.name
            for t in threading.enumerate()
        ), "driver/reaper threads must exit after abandon"

        fake_pacs.move_response_delay = 0.0
        received = list(eng.iter_series(study, s2))  # slot reusable after reaper release
        assert len(received) == len(seeded_study[s2])
    assert not any("unregistered session" in r.message for r in caplog.records)
    scp.stop()
    cache.shutdown()


def _await_session(scp: StorageSCP, key: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with scp._lock:
            if key in scp._sessions:
                return
        time.sleep(0.005)
    raise AssertionError(f"session {key} was not registered within {timeout}s")


@pytest.mark.timeout(60)
def test_same_series_retry_gets_busy_error(
    fake_pacs, seeded_study, free_port, tmp_path
) -> None:
    eng, _pool, scp, cache = _make_engine(
        fake_pacs, free_port, tmp_path, arrival_timeout=15.0, move_lease_timeout=0.4
    )
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    fake_pacs.move_response_delay = 1.5
    result: dict[str, object] = {}

    def consume_first() -> None:
        try:
            result["received"] = list(eng.iter_series(study, series))
        except Exception as e:  # recorded for the assertion below
            result["error"] = e

    first = threading.Thread(target=consume_first)
    first.start()
    try:
        _await_session(scp, f"{study}/{series}")  # first consumer holds the key lock
        start = time.monotonic()
        with pytest.raises(PoolExhaustedError) as excinfo:  # deployed-handler compat
            list(eng.iter_series(study, series))
        assert isinstance(excinfo.value, RetrieveBusyError)
        assert time.monotonic() - start < 1.5
    finally:
        first.join(timeout=30)
    assert "error" not in result, f"first consumer failed: {result.get('error')}"
    assert len(result["received"]) == len(seeded_study[series])
    scp.stop()
    cache.shutdown()
