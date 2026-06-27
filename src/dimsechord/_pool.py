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
    semaphore: threading.Semaphore


class AssociationPool:
    """A pool of AET identities, each capped at ``per_aet_cap`` concurrent associations.

    The leased AET is used as both the calling AET and the C-MOVE destination, so
    returning C-STORE sub-operations arrive addressed to that AET. ``N=1, cap=1``
    reproduces the legacy single-semaphore behaviour.
    """

    def __init__(self, aets: list[str], per_aet_cap: int = 1) -> None:
        if not aets:
            raise ValueError("AssociationPool requires at least one AET")
        if per_aet_cap < 1:
            raise ValueError("per_aet_cap must be >= 1")
        self._per_aet_cap = per_aet_cap
        self._pooled = [
            _PooledAet(aet=a, semaphore=threading.Semaphore(per_aet_cap)) for a in aets
        ]
        self._rr_lock = threading.Lock()
        self._next = 0

    @property
    def aets(self) -> list[str]:
        return [p.aet for p in self._pooled]

    @property
    def total_capacity(self) -> int:
        return len(self._pooled) * self._per_aet_cap

    def _acquire(self, timeout: float | None) -> _PooledAet:
        n = len(self._pooled)
        with self._rr_lock:
            start = self._next
            self._next = (self._next + 1) % n

        # Fast path: try every AET non-blocking, round-robin start.
        for i in range(n):
            pooled = self._pooled[(start + i) % n]
            if pooled.semaphore.acquire(blocking=False):
                return pooled

        # All busy: block on the round-robin-chosen AET (timeout=None → forever).
        pooled = self._pooled[start]
        acquired = pooled.semaphore.acquire(timeout=timeout)
        if not acquired:
            raise PoolExhaustedError(
                f"No association slot available within {timeout}s "
                f"(capacity={self.total_capacity})"
            )
        return pooled

    @contextmanager
    def lease(self, timeout: float | None = None) -> Iterator[str]:
        """Lease one AET for the duration of the ``with`` block."""
        pooled = self._acquire(timeout)
        try:
            yield pooled.aet
        finally:
            pooled.semaphore.release()
