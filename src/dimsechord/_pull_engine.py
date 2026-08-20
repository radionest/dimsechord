"""Move-to-self pull-engine: synchronous core + thin async adapter.

The lower half is synchronous/threaded because pynetdicom is, and because one of
the two consumers — the DIMSE C-MOVE SCP handler — is itself a synchronous
generator running in a pynetdicom worker thread. The async methods are thin
adapters that run the sync iterator in a worker thread and bridge each item to
the event loop, so the HTTP face gets ``async for`` without re-implementing the
thread→async bridge. The miss branch delegates to a transport strategy —
``_MoveToSelfTransport`` (C-MOVE) or ``_CGetTransport`` (C-GET) — while the
cache tiers and per-UID coalescing stay in the engine.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import aclosing
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from dimsechord._bridge import iter_to_aiter
from dimsechord._cache import MemoryCachedSeries
from dimsechord._exceptions import ArrivalTimeoutError, AssociationError, MoveToSelfError
from dimsechord._models import (
    AssociationConfig,
    DicomNode,
    QueryRetrieveLevel,
    RetrieveRequest,
    StorageConfig,
    StorageMode,
)
from dimsechord._scu import DicomOperations, MoveAbortHandle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from pydicom import Dataset

    from dimsechord._cache import DicomCache
    from dimsechord._pool import AssociationPool, _MoveLease
    from dimsechord._scp import MoveSession, StorageSCP

logger = logging.getLogger(__name__)

# Post-abort, a driver parked in the DIMSE receive exits at the AE's dimse
# timeout, not at abort; the short join covers the already-finishing case,
# the reaper covers the rest.
_ABORT_JOIN_TIMEOUT = 2.0


class _MoveToSelfTransport:
    """C-MOVE-to-self retrieval: PACS C-MOVEs to our StorageSCP; we drain the queue.

    Holds the move-specific machinery (association pool, Storage SCP, move thread,
    arrival-timeout). ``stream`` yields ``(sop_uid, dataset)`` as each C-STORE
    arrives (progressive), then re-raises a driver error or, if nothing arrived,
    ``MoveToSelfError``.
    """

    def __init__(
        self,
        *,
        pool: AssociationPool,
        scp: StorageSCP,
        pacs: DicomNode,
        max_pdu: int,
        cmove_timeout: float,
        arrival_timeout: float,
        completion_grace: float,
        move_lease_timeout: float,
    ) -> None:
        self._pool = pool
        self._scp = scp
        self._pacs = pacs
        self._max_pdu = max_pdu
        # Unused since 0.8.0 — cancellation is abort-based; kept for signature stability.
        self._cmove_timeout = cmove_timeout
        self._arrival_timeout = arrival_timeout
        self._completion_grace = completion_grace
        self._move_lease_timeout = move_lease_timeout

    @staticmethod
    def _scp_key(request: RetrieveRequest) -> str:
        if request.level == QueryRetrieveLevel.SERIES:
            return f"{request.study_instance_uid}/{request.series_instance_uid}"
        return f"{request.study_instance_uid}/"

    def stream(self, request: RetrieveRequest) -> Iterator[tuple[str, Dataset]]:
        scp_key = self._scp_key(request)
        # Fail fast BEFORE any upstream work: no slot → typed error in
        # move_lease_timeout, with no session registered and no C-MOVE issued.
        lease = self._pool._acquire_move(timeout=self._move_lease_timeout)
        try:
            session = self._scp.register_session(scp_key)
        except BaseException:
            lease.release()
            raise
        yielded = 0
        move_error: list[Exception] = []
        abort_handle = MoveAbortHandle()
        move_thread = threading.Thread(
            target=self._drive_move,
            args=(scp_key, request, move_error, lease.aet, abort_handle),
            name=f"dimsechord-move-{scp_key}",
            daemon=True,
        )
        try:
            move_thread.start()
        except BaseException:
            # Same shape as the register_session guard above: a thread that
            # never started must not hold the session open (poisons future
            # register_session calls for this key) or the lease (permanently
            # shrinks the pool) — both matter most exactly when the host is
            # under the thread/association pressure that would cause this.
            self._scp.finish_session(scp_key)
            lease.release()
            raise
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
                yielded += 1
                yield item
        finally:
            # Bounds EVERY consumer-gone path (arrival timeout, GeneratorExit,
            # consumer exception): abort the upstream move — a no-op if it
            # already finished — then a short join, never a minutes-long one.
            abort_handle.abort()
            move_thread.join(timeout=_ABORT_JOIN_TIMEOUT)
            self._scp.finish_session(scp_key)
            if move_thread.is_alive():
                # Normal for an abandoned in-flight move: abort cannot wake a
                # parked DIMSE receive, so the driver exits at dimse_timeout.
                logger.warning(
                    f"C-MOVE driver for {scp_key} (AET {lease.aet}) still alive "
                    f"{_ABORT_JOIN_TIMEOUT}s after abort — deferring slot release "
                    "until it exits (expected within the DIMSE timeout)"
                )
                threading.Thread(
                    target=self._reap,
                    args=(move_thread, lease, scp_key),
                    name=f"dimsechord-move-reaper-{scp_key}",
                    daemon=True,
                ).start()
            else:
                lease.release()

        if move_error:
            raise move_error[0]
        if move_thread.is_alive():
            raise AssociationError(
                f"C-MOVE driver for {scp_key} did not finish within "
                f"{_ABORT_JOIN_TIMEOUT}s of abort — shutdown mid-pull or a hung "
                "C-MOVE; refusing to treat the partial delivery as complete."
            )
        if yielded == 0:
            raise MoveToSelfError(
                f"C-MOVE for {scp_key} completed but no instances arrived — "
                "is the PACS configured to route the destination AET back to us?"
            )

    @staticmethod
    def _reap(move_thread: threading.Thread, lease: _MoveLease, scp_key: str) -> None:
        move_thread.join()
        lease.release()
        logger.info(f"C-MOVE driver for {scp_key} exited; move slot released")

    def _drive_move(
        self,
        scp_key: str,
        request: RetrieveRequest,
        error_holder: list,
        aet: str,
        abort_handle: MoveAbortHandle,
    ) -> None:
        try:
            config = AssociationConfig(
                calling_aet=aet,
                called_aet=self._pacs.aet,
                peer_host=self._pacs.host,
                peer_port=self._pacs.port,
            )
            # SCU built per lease so its AE title == the leased AET (the C-MOVE
            # destination); a shared SCU with a fixed calling AET would mismatch
            # the leased AET when the pool holds N > 1 identities.
            ops = DicomOperations(calling_aet=aet, max_pdu=self._max_pdu)
            result = ops.move(config, request, destination_aet=aet, abort_handle=abort_handle)
            if result.num_failed:
                raise AssociationError(
                    f"C-MOVE incomplete: {result.num_failed} sub-operation(s) failed "
                    f"({result.num_completed} completed) — not caching a partial series."
                )
            if result.status != "success":
                raise AssociationError(
                    f"C-MOVE ended with non-success status {result.status!r} "
                    f"({result.num_completed} completed) — the move was aborted, "
                    "refused, or left undetermined; not caching a partial series."
                )
            if result.num_completed:
                self._scp.set_expected(scp_key, result.num_completed)
                if not self._scp.wait_for_completion(scp_key, self._completion_grace):
                    raise AssociationError(
                        f"C-MOVE reported {result.num_completed} completed sub-operation(s) "
                        f"but they did not all arrive within {self._completion_grace}s — "
                        "not caching a partial series."
                    )
        except Exception as e:
            logger.error(f"C-MOVE driver failed for {scp_key}: {e}")
            error_holder.append(e)
        finally:
            self._scp.signal_end(scp_key)

    @staticmethod
    def _blocking_get(session: MoveSession, timeout: float) -> tuple[str, Dataset] | None:
        try:
            return session.queue.get(timeout=timeout)
        except queue.Empty:
            return None


class _CGetTransport:
    """C-GET retrieval transport: in-association, no pool/SCP.

    ``stream`` performs a blocking C-GET to memory and yields the collected
    ``(sop_uid, dataset)`` pairs as a batch (C-GET delivers on one association).
    """

    def __init__(
        self,
        *,
        pacs: DicomNode,
        calling_aet: str,
        max_pdu: int = 16384,
        cget_timeout: float = 300.0,
    ) -> None:
        self._pacs = pacs
        self._calling_aet = calling_aet
        self._max_pdu = max_pdu
        self._cget_timeout = cget_timeout

    def stream(self, request: RetrieveRequest) -> Iterator[tuple[str, Dataset]]:
        ops = DicomOperations(calling_aet=self._calling_aet, max_pdu=self._max_pdu)
        config = AssociationConfig(
            calling_aet=self._calling_aet,
            called_aet=self._pacs.aet,
            peer_host=self._pacs.host,
            peer_port=self._pacs.port,
            max_pdu=self._max_pdu,
            timeout=self._cget_timeout,
        )
        result = ops.retrieve_via_get(config, request, StorageConfig(mode=StorageMode.MEMORY))
        if result.num_failed:
            raise AssociationError(
                f"C-GET incomplete: {result.num_failed} sub-operation(s) failed "
                f"({result.num_completed} completed) — not caching a partial series."
            )
        if result.status != "success":
            raise AssociationError(
                f"C-GET ended with non-success status {result.status!r} "
                f"({result.num_completed} completed) — the retrieve was aborted, "
                "refused, or left undetermined; not caching a partial series."
            )
        yield from result.instances.items()


class PullEngine:
    """Drives C-MOVE-to-self, streams instances to consumers, tees to cache+index."""

    def __init__(
        self,
        pool: AssociationPool,
        scp: StorageSCP,
        cache: DicomCache,
        pacs: DicomNode,
        *,
        max_pdu: int = 16384,
        # Retained for compatibility; since 0.8.0 cancellation is abort-based
        # (move_lease_timeout + arrival_timeout + the fixed abort join).
        cmove_timeout: float = 300.0,
        arrival_timeout: float = 60.0,
        completion_grace: float = 5.0,
        move_lease_timeout: float = 5.0,
    ) -> None:
        self._init(
            _MoveToSelfTransport(
                pool=pool,
                scp=scp,
                pacs=pacs,
                max_pdu=max_pdu,
                cmove_timeout=cmove_timeout,
                arrival_timeout=arrival_timeout,
                completion_grace=completion_grace,
                move_lease_timeout=move_lease_timeout,
            ),
            cache,
            move_lease_timeout,
        )

    def _init(
        self,
        transport: _MoveToSelfTransport | _CGetTransport,
        cache: DicomCache,
        move_lease_timeout: float,
    ) -> None:
        self._transport = transport
        self._cache = cache
        self._move_lease_timeout = move_lease_timeout
        self._locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
        self._registry_lock = threading.Lock()

    @classmethod
    def via_cget(
        cls,
        cache: DicomCache,
        pacs: DicomNode,
        *,
        calling_aet: str,
        max_pdu: int = 16384,
        cget_timeout: float = 300.0,
        move_lease_timeout: float = 5.0,
    ) -> PullEngine:
        """Build a cache-filling engine that retrieves via C-GET (no pool/SCP).

        ``move_lease_timeout`` bounds only the same-key coalescing wait here —
        a C-GET engine has no association pool to lease from.
        """
        eng = cls.__new__(cls)
        eng._init(
            _CGetTransport(
                pacs=pacs,
                calling_aet=calling_aet,
                max_pdu=max_pdu,
                cget_timeout=cget_timeout,
            ),
            cache,
            move_lease_timeout,
        )
        return eng

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

            # 4. Miss → fetch via the configured transport.
            request = RetrieveRequest(
                level=QueryRetrieveLevel.SERIES,
                study_instance_uid=study_uid,
                series_instance_uid=series_uid,
            )
            yield from self._fetch(study_uid, series_uid, request)

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
            request = RetrieveRequest(
                level=QueryRetrieveLevel.STUDY, study_instance_uid=study_uid
            )
            yield from self._fetch(study_uid, None, request)

    def _fetch(
        self, study_uid: str, series_uid: str | None, request: RetrieveRequest
    ) -> Iterator[Dataset]:
        collected: dict[str, Dataset] = {}
        for sop_uid, ds in self._transport.stream(request):
            collected[sop_uid] = ds
            ser = series_uid or str(ds.SeriesInstanceUID)
            self._cache.schedule_tee(study_uid, ser, sop_uid, ds, source="pacs")
            yield ds
        if collected:
            self._finalize_fetch(study_uid, series_uid, collected)

    def _finalize_fetch(
        self, study_uid: str, series_uid: str | None, collected: dict[str, Dataset]
    ) -> None:
        """Commit a cleanly completed fetch: memory tier + disk completeness marker."""
        grouped: dict[str, dict[str, Dataset]]
        if series_uid is not None:
            grouped = {series_uid: collected}
        else:
            grouped = {}
            for sop_uid, ds in collected.items():
                grouped.setdefault(str(ds.SeriesInstanceUID), {})[sop_uid] = ds
        for ser_uid, instances in grouped.items():
            self._cache.put_series_to_memory(
                study_uid, ser_uid, instances, disk_persisted=False
            )
            self._cache.mark_series_complete(study_uid, ser_uid, len(instances))

    # ── async adapters (used by the HTTP face) ───────────────────
    async def stream_series(
        self, study_uid: str, series_uid: str
    ) -> AsyncIterator[Dataset]:
        async with aclosing(
            iter_to_aiter(lambda: self.iter_series(study_uid, series_uid))
        ) as agen:
            async for ds in agen:
                yield ds

    async def stream_study(
        self, study_uid: str, series_uids: list[str]
    ) -> AsyncIterator[Dataset]:
        async with aclosing(
            iter_to_aiter(lambda: self.iter_study(study_uid, series_uids))
        ) as agen:
            async for ds in agen:
                yield ds

    async def ensure_series(self, study_uid: str, series_uid: str) -> MemoryCachedSeries:
        instances: dict[str, Dataset] = {}
        async for ds in self.stream_series(study_uid, series_uid):
            instances[str(ds.SOPInstanceUID)] = ds
        cached = self._cache.get_series_from_memory(study_uid, series_uid)
        if cached is not None:
            return cached
        if not instances:
            return MemoryCachedSeries(
                study_uid=study_uid,
                series_uid=series_uid,
                instances={},
                cached_at=time.time(),
            )
        return self._cache.put_series_to_memory(
            study_uid, series_uid, instances, disk_persisted=False
        )
