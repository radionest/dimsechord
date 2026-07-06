"""Persistent Storage SCP for C-MOVE self-retrieval with a streaming queue."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pynetdicom import AE, ALL_TRANSFER_SYNTAXES, StoragePresentationContexts, evt
from pynetdicom.sop_class import Verification  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydicom import Dataset

logger = logging.getLogger(__name__)


@dataclass
class MoveSession:
    """Tracks instances received for a single C-MOVE request.

    ``queue`` streams ``(sop_uid, dataset)`` as each C-STORE arrives; a ``None``
    sentinel (via ``signal_end``) marks end-of-stream. ``instances`` retains the
    full set for non-streaming callers.
    """

    instances: dict[str, Dataset] = field(default_factory=dict)
    queue: queue.Queue[tuple[str, Dataset] | None] = field(default_factory=queue.Queue)
    expected_count: int | None = None
    received_count: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    ended: bool = False


class StorageSCP:
    """Persistent pynetdicom Storage SCP that feeds per-session streaming queues."""

    def __init__(self, supported_transfer_syntaxes: Sequence[str] = ALL_TRANSFER_SYNTAXES) -> None:
        """Args:
        supported_transfer_syntaxes: Transfer syntaxes every storage context
            accepts (default: all pynetdicom knows). Supported contexts are
            matched against the requester's proposals — never proposed — so
            there is no 128-context limit on the accept side. Accepting all
            syntaxes lets the upstream PACS send compressed objects verbatim
            instead of failing sub-operations or transcoding.
        """
        self._supported_transfer_syntaxes = list(supported_transfer_syntaxes)
        self._servers: list[Any] = []
        self._aes: list[Any] = []
        self._sessions: dict[str, MoveSession] = {}
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return bool(self._servers)

    # ── Lifecycle ──────────────────────────────────────────────────
    def start(self, bindings: Mapping[str, int], ip: str = "0.0.0.0") -> None:
        """Start one Storage SCP listener per distinct port, in background threads.

        Args:
            bindings: AET → port map. Several AETs may share a port; the SCP
                binds one server per distinct port (the first AET on a port is
                its primary title) with ``require_called_aet = False``, so any
                AET routes here. Inbound C-STORE is dispatched by Study/Series
                UID across the shared session registry, regardless of port.
            ip: Bind interface for every listener (default: all interfaces).
        """
        if self._servers:
            logger.warning("Storage SCP already running, skipping start")
            return
        if not bindings:
            raise ValueError("StorageSCP.start requires at least one (AET, port) binding")

        by_port: dict[int, list[str]] = {}
        for aet, port in bindings.items():
            by_port.setdefault(port, []).append(aet)

        handlers = [(evt.EVT_C_STORE, self._handle_store)]
        try:
            for port, aets in by_port.items():
                ae = AE(ae_title=aets[0])
                ae.require_called_aet = False
                for ctx in StoragePresentationContexts:
                    if ctx.abstract_syntax is not None:
                        ae.add_supported_context(
                            ctx.abstract_syntax, self._supported_transfer_syntaxes
                        )
                ae.add_supported_context(Verification)
                server = ae.start_server((ip, port), evt_handlers=handlers, block=False)  # type: ignore[arg-type]
                self._servers.append(server)
                self._aes.append(ae)
                logger.info(f"Storage SCP listening on {ip}:{port} (AETs: {aets})")
        except Exception:
            for server in self._servers:
                server.shutdown()
            self._servers.clear()
            self._aes.clear()
            raise

    def stop(self) -> None:
        for server in self._servers:
            server.shutdown()
        self._servers.clear()
        self._aes.clear()
        with self._lock:
            for session in self._sessions.values():
                session.ended = True
                session.done.set()
                session.queue.put(None)
            self._sessions.clear()
        logger.info("Storage SCP stopped")

    # ── Session management ────────────────────────────────────────
    def register_session(self, key: str) -> MoveSession:
        session = MoveSession()
        with self._lock:
            if key in self._sessions:
                raise RuntimeError(f"C-MOVE session already active for key={key}")
            self._sessions[key] = session
        logger.debug(f"Registered C-MOVE session: {key}")
        return session

    def set_expected(self, key: str, count: int) -> None:
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return
            session.expected_count = count
            if session.received_count >= count:
                session.done.set()

    def wait_for_completion(self, key: str, timeout: float) -> bool:
        """Wait until the session's ``done`` event fires; return whether it did.

        ``True`` means the expected C-STOREs all physically arrived within
        ``timeout``. ``False`` means the grace expired first — a physical-arrival
        shortfall — the session is unknown, or the ``done`` event was fired by
        shutdown/session-end (``stop``/``signal_end``) rather than by genuine
        arrival: a completion satisfied by session-end must read as failure so a
        truncated series is never certified complete. Normal completion sets
        ``done`` via the C-STORE handler with ``ended`` still ``False`` (``signal_end``
        runs only after this returns), so the guard leaves the happy path intact.
        The event is thread-safe, so it is awaited outside ``_lock``.
        """
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return False
        return session.done.wait(timeout=timeout) and not session.ended

    def signal_end(self, key: str) -> None:
        """Mark the session ended and push the end-of-stream sentinel."""
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return
            session.ended = True
            session.done.set()
        session.queue.put(None)

    def finish_session(self, key: str) -> MoveSession | None:
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            logger.debug(
                f"Finished C-MOVE session: {key} (received {session.received_count} instances)"
            )
        return session

    # ── SCP event handler ─────────────────────────────────────────
    def _handle_store(self, event: evt.Event) -> int:
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta
            study_uid = str(getattr(ds, "StudyInstanceUID", ""))
            series_uid = str(getattr(ds, "SeriesInstanceUID", ""))
            sop_uid = str(getattr(ds, "SOPInstanceUID", ""))

            with self._lock:
                session = self._find_session(study_uid, series_uid)
                if session is None:
                    logger.warning(
                        f"SCP received C-STORE for unregistered session: "
                        f"study={study_uid}, series={series_uid}"
                    )
                    return 0x0000
                session.instances[sop_uid] = ds
                session.received_count += 1
                # Enqueue before signalling completion: ``done`` firing lets the driver's
                # ``wait_for_completion`` return and run ``signal_end``, pushing the None
                # sentinel — which must never overtake this item, or the consumer breaks
                # having yielded N-1 of N and certifies a short series. The queue is
                # thread-safe and unbounded (put never blocks), so it is safe under the lock.
                session.queue.put((sop_uid, ds))
                if (
                    session.expected_count is not None
                    and session.received_count >= session.expected_count
                ):
                    session.done.set()
            return 0x0000
        except Exception as e:
            logger.error(f"SCP C-STORE handler error: {e}")
            return 0xC000

    def _find_session(self, study_uid: str, series_uid: str) -> MoveSession | None:
        """Find the matching session. Must be called under ``_lock``.

        Tries the series-level key first, then the study-level key.
        """
        session = self._sessions.get(f"{study_uid}/{series_uid}")
        if session is not None:
            return session
        return self._sessions.get(f"{study_uid}/")
