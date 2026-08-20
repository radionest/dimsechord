import threading
from contextlib import ExitStack

import pytest

from dimsechord._exceptions import PoolExhaustedError
from dimsechord._pool import AssociationPool


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


def test_find_lease_independent_of_move_lease() -> None:
    pool = AssociationPool(aets=["A"], per_aet_cap=1, per_aet_find_cap=1)
    with pool.lease(timeout=1), pool.lease_find(timeout=1) as aet:
        assert aet == "A"


def test_find_cap_exhaustion_does_not_block_move() -> None:
    pool = AssociationPool(aets=["A"], per_aet_cap=1, per_aet_find_cap=1)
    with pool.lease_find(timeout=1):
        with pytest.raises(PoolExhaustedError), pool.lease_find(timeout=0.2):
            pass
        with pool.lease(timeout=1) as aet:
            assert aet == "A"


def test_find_cap_default_is_four() -> None:
    pool = AssociationPool(aets=["A"])
    with ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(pool.lease_find(timeout=0.5))
        with pytest.raises(PoolExhaustedError), pool.lease_find(timeout=0.2):
            pass


def test_find_cap_validation() -> None:
    with pytest.raises(ValueError):
        AssociationPool(aets=["A"], per_aet_find_cap=0)


def test_acquire_move_timeout_raises() -> None:
    pool = AssociationPool(["A"], per_aet_cap=1)
    lease = pool._acquire_move(timeout=1)
    with pytest.raises(PoolExhaustedError):
        pool._acquire_move(timeout=0.1)
    lease.release()


def test_move_lease_release_is_idempotent() -> None:
    pool = AssociationPool(["A"], per_aet_cap=1)
    lease = pool._acquire_move(timeout=1)
    lease.release()
    lease.release()  # double release must NOT grow capacity beyond per_aet_cap
    second = pool._acquire_move(timeout=0.1)
    with pytest.raises(PoolExhaustedError):
        pool._acquire_move(timeout=0.05)
    second.release()


def test_lease_context_manager_still_works_over_handle() -> None:
    pool = AssociationPool(["A", "B"], per_aet_cap=1)
    with pool.lease(timeout=1) as aet:
        assert aet in pool.aets
    # slot returned: full capacity acquirable again
    leases = [pool._acquire_move(timeout=0.5) for _ in range(2)]
    for lease in leases:
        lease.release()
