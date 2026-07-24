"""Persistent association-scoped C-STORE SCU session."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING

from pynetdicom import AE

from dimsechord._exceptions import AssociationError, NoPresentationContextError
from dimsechord._presentation import build_storage_scu_contexts, matches_accepted_context

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from pydicom import Dataset
    from pynetdicom.association import Association
    from pynetdicom.presentation import PresentationContext

    from dimsechord._models import DicomNode

logger = logging.getLogger(__name__)


class StoreSession:
    """One persistent C-STORE association to a peer; statuses pass through verbatim.

    ``store`` returns the peer's DIMSE status as an ``int`` — success, warning
    and failure codes alike; transport problems raise ``AssociationError``.
    The association opens on the first ``store`` and reopens transparently
    only when it is provably dead BEFORE sending, so no instance can ever be
    duplicated silently. ``close`` releases the association and is idempotent;
    a later ``store`` simply reopens.

    A per-instance presentation-context miss raises ``NoPresentationContextError``
    (a ``DimsechordError``, NOT an ``AssociationError``) without sending anything,
    leaving the association open and usable for the next ``store``. A residual
    ``ValueError`` from ``send_c_store`` after that pre-check is a
    dataset-encoding failure — it propagates as-is and the association stays
    intact.

    Sessions are cheap and single-threaded: one session is used by one thread
    at a time (no internal locking) — create one per inbound association.
    They deliberately bypass ``DicomClient.set_max_concurrent_associations``
    (callers govern session concurrency) and disable pynetdicom's 60 s idle
    ``network_timeout`` (a relay association is idle by design), so a session
    that is never closed keeps its association open — always ``close()`` or
    use the context manager.
    """

    def __init__(
        self,
        peer: DicomNode,
        *,
        calling_aet: str,
        timeout: float = 30.0,
        max_pdu: int = 16384,
        contexts: Sequence[PresentationContext] | None = None,
    ) -> None:
        self._peer = peer
        self._calling_aet = calling_aet
        self._timeout = timeout
        self._max_pdu = max_pdu
        self._contexts = list(contexts) if contexts is not None else None
        self._assoc: Association | None = None

    def store(self, dataset: Dataset) -> int:
        sop_class = str(dataset.SOPClassUID)
        transfer_syntax = str(dataset.file_meta.TransferSyntaxUID)
        assoc = self._ensure_association()
        if not matches_accepted_context(assoc.accepted_contexts, sop_class, transfer_syntax):
            raise NoPresentationContextError(sop_class, transfer_syntax)
        # send_c_store's encoder resolves ambiguous VRs (e.g. PixelData) in
        # place; send a copy so the caller's Dataset is never mutated (D10).
        status = assoc.send_c_store(deepcopy(dataset))
        if not status:
            self._discard()
            raise AssociationError(
                "C-STORE got no response (association aborted or timed out); "
                "delivery of this instance is unknown"
            )
        return int(status.Status)

    def close(self) -> None:
        assoc, self._assoc = self._assoc, None
        if assoc is not None and assoc.is_established:
            assoc.release()

    def __enter__(self) -> StoreSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _ensure_association(self) -> Association:
        if self._assoc is not None and self._assoc.is_established:
            return self._assoc
        self._discard()
        ae = AE(ae_title=self._calling_aet)
        ae.maximum_pdu_size = self._max_pdu
        ae.acse_timeout = self._timeout
        ae.dimse_timeout = self._timeout
        # Persistent by design: never idle-abort locally (pynetdicom default
        # network_timeout=60 s would); dead peers are caught pre-send instead.
        ae.network_timeout = None
        ae.requested_contexts = (
            self._contexts if self._contexts is not None else build_storage_scu_contexts()
        )
        assoc = ae.associate(self._peer.host, self._peer.port, ae_title=self._peer.aet)
        if not assoc.is_established:
            raise AssociationError(
                f"Failed to establish DICOM association with {self._peer.aet}"
            )
        self._assoc = assoc
        return assoc

    def _discard(self) -> None:
        assoc, self._assoc = self._assoc, None
        if assoc is not None and assoc.is_established:
            assoc.abort()
