"""Persistent Storage SCP for C-MOVE self-retrieval with a streaming queue."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pynetdicom import AE, StoragePresentationContexts, evt
from pynetdicom.sop_class import Verification  # type: ignore[attr-defined]

if TYPE_CHECKING:
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

    def __init__(self) -> None:
        self._server: Any | None = None
        self._sessions: dict[str, MoveSession] = {}
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._server is not None

    # ── Lifecycle ──────────────────────────────────────────────────
    def start(self, aets: list[str] | str, port: int, ip: str | None = None) -> None:
        """Start the Storage SCP in a background thread.

        Args:
            aets: One AET (str) or the pool's AETs (list). The server binds the
                first as its primary title and accepts C-STORE addressed to any
                AET (``require_called_aet = False``), so every pool AET routes here.
            port: TCP port to bind.
            ip: Bind IP (default: all interfaces).
        """
        if self._server is not None:
            logger.warning("Storage SCP already running, skipping start")
            return

        aet_list = [aets] if isinstance(aets, str) else list(aets)
        if not aet_list:
            raise ValueError("StorageSCP.start requires at least one AET")

        ae = AE(ae_title=aet_list[0])
        ae.require_called_aet = False
        for ctx in StoragePresentationContexts:
            if ctx.abstract_syntax is not None:
                ae.add_supported_context(ctx.abstract_syntax)
        ae.add_supported_context(Verification)

        handlers = [(evt.EVT_C_STORE, self._handle_store)]
        bind_address = (ip or "0.0.0.0", port)
        self._server = ae.start_server(bind_address, evt_handlers=handlers, block=False)  # type: ignore[arg-type]
        logger.info(f"Storage SCP started on {bind_address[0]}:{port} (AETs: {aet_list})")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
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

    def wait_for_completion(self, key: str, timeout: float) -> MoveSession | None:
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return None
        session.done.wait(timeout=timeout)
        return session

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
                if (
                    session.expected_count is not None
                    and session.received_count >= session.expected_count
                ):
                    session.done.set()

            session.queue.put((sop_uid, ds))
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


# ── Module-level singleton ────────────────────────────────────────
_scp: StorageSCP | None = None


def get_storage_scp() -> StorageSCP:
    global _scp
    if _scp is None:
        _scp = StorageSCP()
    return _scp


def shutdown_storage_scp() -> None:
    global _scp
    if _scp is not None:
        _scp.stop()
    _scp = StorageSCP()
