"""AssociationPool: N AET identities with per-AET association caps (D6)."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from dimsechord._exceptions import PoolExhaustedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PooledAet:
    aet: str
    move_semaphore: threading.Semaphore
    find_semaphore: threading.Semaphore


class AssociationPool:
    """A pool of AET identities with independent move and find caps.

    Move leases (``lease``) gate C-MOVE-to-self, where the leased AET doubles
    as the C-MOVE destination and must be exclusive per slot. Find leases
    (``lease_find``) gate plain C-FIND calling identities — a PACS tolerates
    several concurrent associations from one AET, so the cap is independent
    and typically higher. ``N=1, per_aet_cap=1`` reproduces the legacy
    move behaviour.
    """

    def __init__(
        self, aets: list[str], per_aet_cap: int = 1, per_aet_find_cap: int = 4
    ) -> None:
        if not aets:
            raise ValueError("AssociationPool requires at least one AET")
        if per_aet_cap < 1:
            raise ValueError("per_aet_cap must be >= 1")
        if per_aet_find_cap < 1:
            raise ValueError("per_aet_find_cap must be >= 1")
        self._per_aet_cap = per_aet_cap
        self._per_aet_find_cap = per_aet_find_cap
        self._pooled = [
            _PooledAet(
                aet=a,
                move_semaphore=threading.Semaphore(per_aet_cap),
                find_semaphore=threading.Semaphore(per_aet_find_cap),
            )
            for a in aets
        ]
        self._rr_lock = threading.Lock()
        self._next = 0

    @property
    def aets(self) -> list[str]:
        return [p.aet for p in self._pooled]

    @property
    def total_capacity(self) -> int:
        return len(self._pooled) * self._per_aet_cap

    def _acquire(self, timeout: float | None, kind: str) -> _PooledAet:
        n = len(self._pooled)
        with self._rr_lock:
            start = self._next
            self._next = (self._next + 1) % n

        def sem(p: _PooledAet) -> threading.Semaphore:
            return p.move_semaphore if kind == "move" else p.find_semaphore

        # Fast path: try every AET non-blocking, round-robin start.
        for i in range(n):
            pooled = self._pooled[(start + i) % n]
            if sem(pooled).acquire(blocking=False):
                return pooled

        # All busy: block on the round-robin-chosen AET (timeout=None → forever).
        pooled = self._pooled[start]
        acquired = sem(pooled).acquire(timeout=timeout)
        if not acquired:
            raise PoolExhaustedError(
                f"No {kind} association slot available within {timeout}s"
            )
        return pooled

    @contextmanager
    def lease(self, timeout: float | None = None) -> Iterator[str]:
        """Lease one AET for a C-MOVE-to-self for the duration of the block."""
        pooled = self._acquire(timeout, "move")
        try:
            yield pooled.aet
        finally:
            pooled.move_semaphore.release()

    @contextmanager
    def lease_find(self, timeout: float | None = None) -> Iterator[str]:
        """Lease one AET identity for a C-FIND for the duration of the block."""
        pooled = self._acquire(timeout, "find")
        try:
            yield pooled.aet
        finally:
            pooled.find_semaphore.release()
