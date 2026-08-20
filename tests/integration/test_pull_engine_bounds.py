"""Repro-style bounds tests for the retrieve path (incident: bounded-retrieve-path)."""

import time

import pytest

from dimsechord._cache import DicomCache
from dimsechord._exceptions import RetrieveBusyError
from dimsechord._models import DicomNode
from dimsechord._pull_engine import PullEngine


def test_cget_engine_coalescing_bound(tmp_path) -> None:
    """The busy bound is transport-independent: a held per-key lock on a C-GET
    engine raises RetrieveBusyError within move_lease_timeout."""
    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    eng = PullEngine.via_cget(
        cache, DicomNode(aet="P", host="127.0.0.1", port=104),
        calling_aet="GETTER", move_lease_timeout=0.2,
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
