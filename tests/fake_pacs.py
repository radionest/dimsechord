"""In-process pynetdicom fake PACS: C-FIND + C-MOVE + C-GET + C-ECHO."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pynetdicom.presentation import PresentationContext

import threading
import time

from pydicom import Dataset
from pynetdicom import AE, ALL_TRANSFER_SYNTAXES, StoragePresentationContexts, evt
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
    Verification,
)

from dimsechord import build_storage_scp_contexts, build_storage_scu_contexts


class FakePacs:
    """A minimal C-MOVE-only PACS for integration tests."""

    def __init__(self, aet: str = "FAKEPACS") -> None:
        self.aet = aet
        self._instances: list[Dataset] = []
        self._destinations: dict[str, tuple[str, int]] = {}
        self._server: object | None = None
        self.moves: list[tuple[str, int]] = []
        self.find_identifiers: list[Dataset] = []
        self.find_calling_aets: list[str] = []
        self.find_contexts: list[str] = []
        self.move_contexts: list[str] = []
        self.get_contexts: list[str] = []
        self.move_identifiers: list[Dataset] = []
        self.get_identifiers: list[Dataset] = []
        self.find_response_delay: float = 0.0
        self.move_response_delay: float = 0.0
        self.fail_find_with: int | None = None
        self.active_associations = 0
        self._assoc_lock = threading.Lock()

    # ── seeding ──────────────────────────────────────────────────
    def add_instance(self, ds: Dataset) -> None:
        self._instances.append(ds)

    def register_destination(self, aet: str, host: str, port: int) -> None:
        """Map a C-MOVE destination AET to a reachable host:port."""
        self._destinations[aet] = (host, port)

    # ── lifecycle ────────────────────────────────────────────────
    def start(
        self,
        port: int,
        require_calling_aets: list[str] | None = None,
        qr_contexts: list[str] | None = None,
    ) -> None:
        ae = AE(ae_title=self.aet)
        if require_calling_aets is not None:
            ae.require_calling_aet = list(require_calling_aets)
        if qr_contexts is None:
            qr_contexts = [
                PatientRootQueryRetrieveInformationModelFind,
                StudyRootQueryRetrieveInformationModelFind,
                PatientRootQueryRetrieveInformationModelMove,
                StudyRootQueryRetrieveInformationModelMove,
                PatientRootQueryRetrieveInformationModelGet,
                StudyRootQueryRetrieveInformationModelGet,
            ]
        for qr_cx in qr_contexts:
            ae.add_supported_context(qr_cx)
        ae.add_supported_context(Verification)
        # C-MOVE sends C-STORE to a separate destination (requested context); C-GET
        # sends C-STORE back over the SAME association, so the storage contexts must
        # accept the requestor's SCP role (scu_role=True lets us act as Storage SCU).
        for cx in StoragePresentationContexts:
            if cx.abstract_syntax is not None:
                ae.add_supported_context(
                    cx.abstract_syntax, ALL_TRANSFER_SYNTAXES, scu_role=True, scp_role=True
                )
        # The C-MOVE store leg opens a NEW association using these requested
        # contexts; compressed pairs must be negotiated explicitly.
        for cx in build_storage_scu_contexts():
            if cx.abstract_syntax is not None:
                ae.add_requested_context(cx.abstract_syntax, cx.transfer_syntax)
        handlers = [
            (evt.EVT_C_FIND, self._on_find),
            (evt.EVT_C_MOVE, self._on_move),
            (evt.EVT_C_GET, self._on_get),
            (evt.EVT_C_ECHO, self._on_echo),
            (evt.EVT_ESTABLISHED, self._on_established),
            (evt.EVT_RELEASED, self._on_closed),
            (evt.EVT_ABORTED, self._on_closed),
        ]
        self._server = ae.start_server(
            ("127.0.0.1", port), block=False, evt_handlers=handlers
        )

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()  # type: ignore[attr-defined]
            self._server = None

    # ── handlers ─────────────────────────────────────────────────
    @staticmethod
    def _on_echo(event: evt.Event) -> int:  # noqa: ARG004
        return 0x0000

    def _on_established(self, event: evt.Event) -> None:  # noqa: ARG002
        with self._assoc_lock:
            self.active_associations += 1

    def _on_closed(self, event: evt.Event) -> None:  # noqa: ARG002
        with self._assoc_lock:
            self.active_associations -= 1

    def _match(self, identifier: Dataset) -> list[Dataset]:
        study = str(getattr(identifier, "StudyInstanceUID", "") or "")
        series = str(getattr(identifier, "SeriesInstanceUID", "") or "")
        out: list[Dataset] = []
        for ds in self._instances:
            if study and str(ds.StudyInstanceUID) != study:
                continue
            if series and str(ds.SeriesInstanceUID) != series:
                continue
            out.append(ds)
        return out

    def _on_find(self, event: evt.Event) -> Iterator[tuple[int, Dataset | None]]:
        identifier = event.identifier
        self.find_contexts.append(str(event.context.abstract_syntax))
        level = str(getattr(identifier, "QueryRetrieveLevel", "STUDY"))
        calling = event.assoc.requestor.ae_title
        if hasattr(calling, "decode"):
            calling = calling.decode()
        self.find_identifiers.append(identifier)
        self.find_calling_aets.append(str(calling).strip())
        if self.fail_find_with is not None:
            yield (self.fail_find_with, None)
            return
        matches = self._match(identifier)

        seen: set[str] = set()
        for ds in matches:
            if level == "STUDY":
                key = str(ds.StudyInstanceUID)
            elif level == "SERIES":
                key = f"{ds.StudyInstanceUID}/{ds.SeriesInstanceUID}"
            else:  # IMAGE
                key = str(ds.SOPInstanceUID)
            if key in seen:
                continue
            seen.add(key)

            resp = Dataset()
            resp.SpecificCharacterSet = "ISO_IR 192"
            resp.QueryRetrieveLevel = level
            resp.StudyInstanceUID = ds.StudyInstanceUID
            if level == "STUDY":
                resp.PatientID = ds.PatientID
                resp.PatientName = ds.PatientName
                resp.StudyDescription = getattr(ds, "StudyDescription", "")
                resp.ModalitiesInStudy = ds.Modality
                series_uids = {
                    str(i.SeriesInstanceUID)
                    for i in self._instances
                    if str(i.StudyInstanceUID) == str(ds.StudyInstanceUID)
                }
                resp.NumberOfStudyRelatedSeries = len(series_uids)
                resp.NumberOfStudyRelatedInstances = sum(
                    1
                    for i in self._instances
                    if str(i.StudyInstanceUID) == str(ds.StudyInstanceUID)
                )
                for attr in (
                    "PatientBirthDate",
                    "PatientSex",
                    "StudyID",
                    "ReferringPhysicianName",
                    "InstitutionName",
                    "StationName",
                    "SOPClassesInStudy",
                ):
                    if getattr(ds, attr, None) is not None:
                        setattr(resp, attr, getattr(ds, attr))
            elif level == "SERIES":
                resp.SeriesInstanceUID = ds.SeriesInstanceUID
                resp.Modality = ds.Modality
                resp.SeriesNumber = ds.SeriesNumber
                resp.SeriesDescription = getattr(ds, "SeriesDescription", "")
                resp.NumberOfSeriesRelatedInstances = sum(
                    1
                    for i in self._instances
                    if str(i.SeriesInstanceUID) == str(ds.SeriesInstanceUID)
                )
                for attr in (
                    "BodyPartExamined",
                    "ProtocolName",
                    "SeriesDate",
                    "OperatorsName",
                    "PerformedProcedureStepDescription",
                ):
                    if getattr(ds, attr, None) is not None:
                        setattr(resp, attr, getattr(ds, attr))
            else:  # IMAGE
                resp.SeriesInstanceUID = ds.SeriesInstanceUID
                resp.SOPInstanceUID = ds.SOPInstanceUID
                resp.SOPClassUID = ds.SOPClassUID
                resp.InstanceNumber = ds.InstanceNumber
                for attr in ("ImageType", "ContentDate", "SliceThickness"):
                    if getattr(ds, attr, None) is not None:
                        setattr(resp, attr, getattr(ds, attr))
            if self.find_response_delay:
                time.sleep(self.find_response_delay)
            yield (0xFF00, resp)
        yield (0x0000, None)

    def _on_move(
        self, event: evt.Event
    ) -> Iterator[tuple[str | None, int] | int | tuple[int, Dataset | None]]:
        self.move_contexts.append(str(event.context.abstract_syntax))
        self.move_identifiers.append(event.identifier)
        dest_raw = event.move_destination
        dest_aet = (
            dest_raw.decode().strip() if isinstance(dest_raw, bytes) else str(dest_raw).strip()
        )
        dest = self._destinations.get(dest_aet)
        if dest is None:
            yield (None, None)  # unknown move destination → 0xA801
            return
        self.moves.append((dest_aet, dest[1]))  # record the AET/port actually routed
        yield (dest[0], dest[1])  # 1st yield: destination (addr, port)
        matches = self._match(event.identifier)
        yield len(matches)  # 2nd yield: number of C-STORE sub-operations
        for ds in matches:
            if self.move_response_delay:
                time.sleep(self.move_response_delay)
            yield (0xFF00, ds)  # pending: send this instance

    def _on_get(
        self, event: evt.Event
    ) -> Iterator[int | tuple[int, Dataset | None]]:
        self.get_contexts.append(str(event.context.abstract_syntax))
        self.get_identifiers.append(event.identifier)
        matches = self._match(event.identifier)
        yield len(matches)  # 1st yield: number of C-STORE sub-operations
        for ds in matches:
            yield (0xFF00, ds)  # pynetdicom sends each as C-STORE on this association


class ScriptedStoreScp:
    """Scripted C-STORE SCP: per-instance status script plus failure injection."""

    def __init__(self, contexts: list[PresentationContext] | None = None) -> None:
        self.statuses: list[int] = []
        self.abort_next = False
        self.received: list[tuple[str, str]] = []
        self.associations = 0
        self._contexts = contexts if contexts is not None else build_storage_scp_contexts()
        self._server: object | None = None
        self._lock = threading.Lock()

    def start(self, port: int) -> None:
        ae = AE(ae_title="STORESCP")
        for cx in self._contexts:
            if cx.abstract_syntax is not None:
                ae.add_supported_context(cx.abstract_syntax, cx.transfer_syntax)
        handlers = [
            (evt.EVT_C_STORE, self._on_store),
            (evt.EVT_ESTABLISHED, self._on_established),
        ]
        self._server = ae.start_server(("127.0.0.1", port), block=False, evt_handlers=handlers)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()  # type: ignore[attr-defined]
            self._server = None

    def abort_all(self) -> None:
        """Server-side abort of every live association (drop-between-stores)."""
        for assoc in list(self._server.active_associations):  # type: ignore[attr-defined]
            assoc.abort()

    def _on_established(self, event: evt.Event) -> None:  # noqa: ARG002
        with self._lock:
            self.associations += 1

    def _on_store(self, event: evt.Event) -> int:
        with self._lock:
            self.received.append(
                (str(event.dataset.SOPInstanceUID), str(event.file_meta.TransferSyntaxUID))
            )
            if self.abort_next:
                self.abort_next = False
                event.assoc.abort()
                return 0x0000  # abort wins the race; the peer never sees this
            return self.statuses.pop(0) if self.statuses else 0x0000
