"""Move-to-self pull-engine: synchronous core + thin async adapter.

The lower half is synchronous/threaded because pynetdicom is, and because one of
the two consumers — the DIMSE C-MOVE SCP handler — is itself a synchronous
generator running in a pynetdicom worker thread. The async methods are thin
adapters that run the sync iterator in a worker thread and bridge each item to
the event loop, so the HTTP face gets ``async for`` without re-implementing the
thread→async bridge. Per-UID coalescing uses a ``threading.Lock`` so it holds
across both faces (§6).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import TYPE_CHECKING

from dimsechord.exceptions import ArrivalTimeoutError, MoveToSelfError
from dimsechord.models import (
    AssociationConfig,
    DicomNode,
    QueryRetrieveLevel,
    RetrieveRequest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from pydicom import Dataset

    from dimsechord.cache import DicomCache, MemoryCachedSeries
    from dimsechord.index import CacheIndex
    from dimsechord.pool import AssociationPool
    from dimsechord.scp import MoveSession, StorageSCP
    from dimsechord.scu import DicomOperations

logger = logging.getLogger(__name__)


class PullEngine:
    """Drives C-MOVE-to-self, streams instances to consumers, tees to cache+index."""

    def __init__(
        self,
        pool: AssociationPool,
        scp: StorageSCP,
        cache: DicomCache,
        index: CacheIndex,
        ops: DicomOperations,
        pacs: DicomNode,
        *,
        cmove_timeout: float = 300.0,
        arrival_timeout: float = 60.0,
        completion_grace: float = 5.0,
    ) -> None:
        self._pool = pool
        self._scp = scp
        self._cache = cache
        self._index = index
        self._ops = ops
        self._pacs = pacs
        self._cmove_timeout = cmove_timeout
        self._arrival_timeout = arrival_timeout
        self._completion_grace = completion_grace
        self._locks: dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()

    def _series_key(self, study_uid: str, series_uid: str) -> str:
        return f"{study_uid}/{series_uid}"

    def _get_lock(self, key: str) -> threading.Lock:
        with self._registry_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    # ── sync core (used directly by the DIMSE C-MOVE generator) ──
    def iter_series(self, study_uid: str, series_uid: str) -> Iterator[Dataset]:
        # 1. Memory hit (no lock).
        cached = self._cache.get_series_from_memory(study_uid, series_uid)
        if cached is not None:
            yield from cached.instances.values()
            return

        with self._get_lock(self._series_key(study_uid, series_uid)):
            # 2. Double-check memory after acquiring the lock (coalescing).
            cached = self._cache.get_series_from_memory(study_uid, series_uid)
            if cached is not None:
                yield from cached.instances.values()
                return

            # 3. Disk hit (index-backed).
            disk = self._cache.load_series_from_disk(study_uid, series_uid)
            if disk is not None:
                self._cache.put_series_to_memory(
                    study_uid, series_uid, disk, disk_persisted=True
                )
                yield from disk.values()
                return

            # 4. Miss → move-to-self streaming.
            yield from self._pull_streaming(
                study_uid=study_uid,
                series_uid=series_uid,
                request=RetrieveRequest(
                    level=QueryRetrieveLevel.SERIES,
                    study_instance_uid=study_uid,
                    series_instance_uid=series_uid,
                ),
                scp_key=self._series_key(study_uid, series_uid),
            )

    def iter_study(self, study_uid: str, series_uids: list[str]) -> Iterator[Dataset]:
        # Fast path: every requested series already in memory.
        if series_uids and all(
            self._cache.get_series_from_memory(study_uid, s) is not None for s in series_uids
        ):
            for s in series_uids:
                cached = self._cache.get_series_from_memory(study_uid, s)
                if cached is not None:
                    yield from cached.instances.values()
            return

        with self._get_lock(f"{study_uid}/__STUDY__"):
            yield from self._pull_streaming(
                study_uid=study_uid,
                series_uid=None,
                request=RetrieveRequest(
                    level=QueryRetrieveLevel.STUDY, study_instance_uid=study_uid
                ),
                scp_key=f"{study_uid}/",
            )

    def _pull_streaming(
        self,
        *,
        study_uid: str,
        series_uid: str | None,
        request: RetrieveRequest,
        scp_key: str,
    ) -> Iterator[Dataset]:
        session = self._scp.register_session(scp_key)
        collected: dict[str, Dataset] = {}
        move_thread = threading.Thread(
            target=self._drive_move,
            args=(scp_key, request),
            name=f"dimsechord-move-{scp_key}",
            daemon=True,
        )
        move_thread.start()
        try:
            while True:
                item = self._blocking_get(session, self._arrival_timeout)
                if item is None:
                    if session.ended:
                        break
                    raise ArrivalTimeoutError(
                        f"No instance for {scp_key} within {self._arrival_timeout}s "
                        f"(received {session.received_count})"
                    )
                sop_uid, ds = item
                collected[sop_uid] = ds
                ser = series_uid or str(ds.SeriesInstanceUID)
                self._cache.schedule_tee(study_uid, ser, sop_uid, ds, source="pacs")
                yield ds
        finally:
            move_thread.join(timeout=self._cmove_timeout)
            self._scp.finish_session(scp_key)

        if not collected:
            raise MoveToSelfError(
                f"C-MOVE for {scp_key} completed but no instances arrived — "
                "is the PACS configured to route the destination AET back to us?"
            )

        # Populate the memory tier so the next request (either face) is an O(1) hit.
        if series_uid is not None:
            self._cache.put_series_to_memory(
                study_uid, series_uid, collected, disk_persisted=False
            )
        else:
            grouped: dict[str, dict[str, Dataset]] = {}
            for sop_uid, ds in collected.items():
                grouped.setdefault(str(ds.SeriesInstanceUID), {})[sop_uid] = ds
            for ser_uid, instances in grouped.items():
                self._cache.put_series_to_memory(
                    study_uid, ser_uid, instances, disk_persisted=False
                )

    def _drive_move(self, scp_key: str, request: RetrieveRequest) -> None:
        try:
            with self._pool.lease(timeout=self._cmove_timeout) as aet:
                config = AssociationConfig(
                    calling_aet=aet,
                    called_aet=self._pacs.aet,
                    peer_host=self._pacs.host,
                    peer_port=self._pacs.port,
                )
                result = self._ops.move_study(config, request, destination_aet=aet)
                if result.num_completed:
                    self._scp.set_expected(scp_key, result.num_completed)
                    self._scp.wait_for_completion(scp_key, self._completion_grace)
        except Exception as e:
            logger.error(f"C-MOVE driver failed for {scp_key}: {e}")
        finally:
            self._scp.signal_end(scp_key)

    @staticmethod
    def _blocking_get(session: MoveSession, timeout: float) -> tuple[str, Dataset] | None:
        try:
            return session.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ── async adapters (used by the HTTP face) ───────────────────
    async def stream_series(
        self, study_uid: str, series_uid: str
    ) -> AsyncIterator[Dataset]:
        async for ds in self._bridge(lambda: self.iter_series(study_uid, series_uid)):
            yield ds

    async def stream_study(
        self, study_uid: str, series_uids: list[str]
    ) -> AsyncIterator[Dataset]:
        async for ds in self._bridge(lambda: self.iter_study(study_uid, series_uids)):
            yield ds

    async def ensure_series(self, study_uid: str, series_uid: str) -> MemoryCachedSeries:
        instances: dict[str, Dataset] = {}
        async for ds in self.stream_series(study_uid, series_uid):
            instances[str(ds.SOPInstanceUID)] = ds
        cached = self._cache.get_series_from_memory(study_uid, series_uid)
        if cached is not None:
            return cached
        return self._cache.put_series_to_memory(
            study_uid, series_uid, instances, disk_persisted=False
        )

    async def _bridge(
        self, make_iter: Callable[[], Iterator[Dataset]]
    ) -> AsyncIterator[Dataset]:
        """Run a sync iterator in a worker thread, stream its items to the loop."""
        loop = asyncio.get_running_loop()
        aq: asyncio.Queue[Dataset | BaseException | object] = asyncio.Queue()
        sentinel = object()

        def _producer() -> None:
            try:
                for ds in make_iter():
                    loop.call_soon_threadsafe(aq.put_nowait, ds)
            except BaseException as e:  # propagate to the async consumer
                loop.call_soon_threadsafe(aq.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(aq.put_nowait, sentinel)

        task = asyncio.create_task(asyncio.to_thread(_producer))
        try:
            while True:
                item = await aq.get()
                if item is sentinel:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item  # type: ignore[misc]
        finally:
            await task
