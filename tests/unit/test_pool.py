import threading

import pytest

from dimsechord.exceptions import PoolExhaustedError
from dimsechord.pool import AssociationPool


def test_empty_aets_rejected() -> None:
    with pytest.raises(ValueError):
        AssociationPool(aets=[])


def test_capacity_and_aets() -> None:
    pool = AssociationPool(aets=["A", "B"], per_aet_cap=2)
    assert pool.aets == ["A", "B"]
    assert pool.total_capacity == 4


def test_lease_yields_pool_aet() -> None:
    pool = AssociationPool(aets=["A", "B"])
    with pool.lease() as aet:
        assert aet in {"A", "B"}


def test_lease_blocks_beyond_capacity_then_recovers() -> None:
    pool = AssociationPool(aets=["A"], per_aet_cap=1)  # capacity 1
    held = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with pool.lease():
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=_hold)
    t.start()
    assert held.wait(timeout=5)

    # Capacity exhausted → a short-timeout lease must raise.
    with pytest.raises(PoolExhaustedError), pool.lease(timeout=0.2):
        pass

    release.set()
    t.join(timeout=5)
    # Slot is free again.
    with pool.lease(timeout=2) as aet:
        assert aet == "A"


def test_two_aets_allow_two_concurrent_leases() -> None:
    pool = AssociationPool(aets=["A", "B"], per_aet_cap=1)  # capacity 2
    seen: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    def _worker() -> None:
        with pool.lease(timeout=3) as aet:
            with lock:
                seen.append(aet)
            barrier.wait()  # both must hold a lease simultaneously

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(seen) == ["A", "B"]
