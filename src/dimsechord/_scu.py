"""Synchronous DICOM operations using pynetdicom (C-FIND / C-MOVE / C-STORE)."""

# Internal API — NOT part of dimsechord's public surface (see ``dimsechord/__init__.py``).
# Before importing ``DicomOperations`` into a consumer, stop and
# reconsider: the public façade — ``DicomClient`` (async C-FIND / C-STORE / C-MOVE) and
# ``PullEngine`` (move-to-self retrieval) — is meant to cover every supported use case.
# If it genuinely does not, EXTEND the public surface (add a method/function and export
# it from ``__init__``) rather than reaching in here. Direct imports of this module are
# unsupported and may change or break without notice.

import logging
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from pydicom import Dataset
from pydicom.multival import MultiValue
from pynetdicom import AE, StoragePresentationContexts, build_role
from pynetdicom.pdu_primitives import SCP_SCU_RoleSelectionNegotiation
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
)

if TYPE_CHECKING:
    from dimsechord._scp import StorageSCP

from dimsechord._exceptions import AssociationError
from dimsechord._handlers import create_store_handler
from dimsechord._models import (
    MODALITIES_SEPARATOR,
    AssociationConfig,
    BatchStoreResult,
    ImageQuery,
    ImageResult,
    QueryRetrieveLevel,
    RetrieveRequest,
    RetrieveResult,
    SeriesQuery,
    SeriesResult,
    StorageConfig,
    StorageMode,
    StudyQuery,
    StudyResult,
)

logger = logging.getLogger(__name__)


def _ds_str(ds: Dataset, attr: str) -> str | None:
    """Get a DICOM attribute as a plain string."""
    val: Any = getattr(ds, attr, None)
    if val is None or val == "":
        return None
    return str(val)


def _ds_int(ds: Dataset, attr: str) -> int | None:
    """Get a DICOM attribute as an int."""
    val: Any = getattr(ds, attr, None)
    if val is None or val == "":
        return None
    return int(val)


def _ds_float(ds: Dataset, attr: str) -> float | None:
    """Get a DICOM attribute as a float (DS decimal).

    Returns ``None`` for a missing/empty value. Non-numeric DS values log a
    warning and return ``None`` rather than raising — slice thickness and
    similar DS fields are best-effort metadata, never worth aborting a parse.
    """
    val: Any = getattr(ds, attr, None)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        logger.warning(f"_ds_float: {attr} is not numeric ({val!r}); returning None")
        return None


def _ds_str_list(ds: Dataset, attr: str) -> list[str] | None:
    """Get a multi-valued DICOM attribute as ``list[str]``.

    For tags like ``SOPClassesInStudy`` / ``ImageType`` / ``OperatorsName``:
    pydicom returns a ``MultiValue`` for VM>1, or a scalar (``str``, ``UID``,
    ``PersonName``, ...) for a single value. Only a ``MultiValue`` is
    iterated element-by-element; a scalar is wrapped as one element via
    ``str()`` rather than iterated directly — ``PersonName`` is not a
    ``str`` subclass, so naive iteration walks it character-by-character
    (``"OPER^X"`` -> ``['O', 'P', 'E', 'R', '^', 'X']``). Unlike
    ``_ds_modalities`` this keeps the list form — these tags are not
    DB-indexed, so there is no ``\\``-joined wire-form constraint. Returns
    ``None`` for a missing/empty value.
    """
    val: Any = getattr(ds, attr, None)
    if val is None or val == "":
        return None
    if isinstance(val, MultiValue):
        items = [str(v) for v in val]
        return items or None
    return [str(val)]


def _ds_modalities(ds: Dataset) -> str | None:
    """Get ``ModalitiesInStudy`` as a DICOM ``\\``-joined string.

    pydicom returns ``MultiValue`` for multi-valued CS tags. Plain
    ``str(MultiValue([...]))`` yields a Python list repr
    (``"['CT', 'SR']"``) which is unparseable downstream. Joining with
    ``MODALITIES_SEPARATOR`` (the DICOM PS3.5 §6.4 value-multiplicity
    separator) keeps the DB value byte-identical to the wire form, so
    DICOMweb re-serialisation is a free split. Callers that need a
    filesystem-safe string should convert the separator themselves.

    Returns ``None`` on a non-iterable, non-string value rather than
    falling back to ``str(val)`` — the previous fallback wrote
    ``"['CT', 'SR']"`` into the DB on edge-case pydicom values, poisoning
    every downstream path renderer that splits on ``MODALITIES_SEPARATOR``.
    """
    val: Any = getattr(ds, "ModalitiesInStudy", None)
    if val is None or val == "":
        return None
    if isinstance(val, str):
        return val
    try:
        return MODALITIES_SEPARATOR.join(str(v) for v in val)
    except TypeError:
        logger.warning(
            f"_ds_modalities: ModalitiesInStudy is not iterable "
            f"({type(val).__name__}); returning None to avoid Python-repr "
            f"leak (value={val!r})"
        )
        return None


def _set_ds_fields(ds: Dataset, fields: dict[str, Any]) -> None:
    """Set DICOM dataset fields, using empty string for None values."""
    for attr, value in fields.items():
        setattr(ds, attr, value if value is not None else "")


_UTF8_CHARSET = "ISO_IR 192"


def _pin_charset(ds: Dataset) -> Dataset:
    """Pin UTF-8 (ISO_IR 192) on an answer dataset before value extraction (D9)."""
    ds.SpecificCharacterSet = _UTF8_CHARSET
    return ds


class DicomOperations:
    """Synchronous DICOM operations wrapper for pynetdicom."""

    _association_semaphore: threading.Semaphore | None = None

    @classmethod
    def set_association_semaphore(cls, max_concurrent: int) -> None:
        """Set global semaphore to limit concurrent DICOM associations.

        Args:
            max_concurrent: Maximum number of concurrent associations
        """
        cls._association_semaphore = threading.Semaphore(max_concurrent)
        logger.info(f"DICOM association semaphore set to {max_concurrent}")

    def __init__(self, calling_aet: str, max_pdu: int = 16384):
        """Initialize DICOM operations.

        Args:
            calling_aet: Calling AE title
            max_pdu: Maximum PDU size (0 for unlimited)
        """
        self.calling_aet = calling_aet
        self.max_pdu = max_pdu

    def _create_ae(self) -> AE:
        """Create Application Entity for C-FIND / C-MOVE operations."""
        ae = AE(ae_title=self.calling_aet)
        ae.maximum_pdu_size = self.max_pdu
        ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
        ae.add_requested_context(PatientRootQueryRetrieveInformationModelMove)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
        return ae

    def _create_get_ae(self) -> tuple[AE, list[SCP_SCU_RoleSelectionNegotiation]]:
        """Create an AE for C-GET with SCP/SCU role negotiation.

        C-GET delivers instances as C-STORE sub-operations over the SAME
        association, so we request SCP role for each storage presentation context.
        Capped at 126 to leave room for the two GET contexts within the DICOM
        128-context limit.
        """
        ae = AE(ae_title=self.calling_aet)
        ae.maximum_pdu_size = self.max_pdu
        ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        roles: list[SCP_SCU_RoleSelectionNegotiation] = []
        for cx in StoragePresentationContexts[:126]:
            if cx.abstract_syntax is not None:
                ae.add_requested_context(cx.abstract_syntax)
                roles.append(build_role(cx.abstract_syntax, scp_role=True))
        dropped = len(StoragePresentationContexts) - 126
        if dropped > 0:
            logger.warning(
                f"C-GET: {dropped} storage presentation context(s) dropped to fit the "
                f"DICOM 128-context limit; those SOP classes cannot be retrieved via C-GET."
            )
        return ae, roles

    @contextmanager
    def _association(
        self,
        ae: AE,
        config: AssociationConfig,
        **kwargs: Any,
    ) -> Generator[Any]:
        """Establish a DICOM association and guarantee release.

        Acquires the global semaphore (if configured) before establishing the
        association, limiting the total number of concurrent DICOM connections.

        Args:
            ae: Application Entity to associate with
            config: Association configuration (peer host, port, AET)
            **kwargs: Extra arguments for ae.associate() (evt_handlers, ext_neg)

        Yields:
            Established association

        Raises:
            AssociationError: If association cannot be established
        """
        semaphore = DicomOperations._association_semaphore
        if semaphore is not None:
            semaphore.acquire()
        try:
            assoc = ae.associate(
                config.peer_host,
                config.peer_port,
                ae_title=config.called_aet,
                **kwargs,
            )
            if not assoc.is_established:
                logger.error(f"Failed to establish association with {config.called_aet}")
                raise AssociationError("Failed to establish DICOM association")
            try:
                yield assoc
            finally:
                assoc.release()
        finally:
            if semaphore is not None:
                semaphore.release()

    def _build_study_query_dataset(self, query: StudyQuery) -> Dataset:
        """Build DICOM dataset for study-level C-FIND.

        Args:
            query: Study query parameters

        Returns:
            DICOM dataset for query
        """
        ds = Dataset()
        ds.SpecificCharacterSet = _UTF8_CHARSET
        ds.QueryRetrieveLevel = QueryRetrieveLevel.STUDY.value

        _set_ds_fields(
            ds,
            {
                "PatientID": query.patient_id,
                "PatientName": query.patient_name,
                "StudyInstanceUID": query.study_instance_uid,
                "StudyDate": query.study_date,
                "StudyDescription": query.study_description,
                "AccessionNumber": query.accession_number,
                "ModalitiesInStudy": query.modality,
                "StudyTime": None,
                "NumberOfStudyRelatedSeries": None,
                "NumberOfStudyRelatedInstances": None,
                # Request the extended standard fields upstream — a standard
                # SCP only returns attributes present in the query identifier.
                "PatientBirthDate": None,
                "PatientSex": None,
                "StudyID": None,
                "ReferringPhysicianName": None,
                "InstitutionName": None,
                "StationName": None,
                "SOPClassesInStudy": None,
            },
        )

        return ds

    def _build_series_query_dataset(self, query: SeriesQuery) -> Dataset:
        """Build DICOM dataset for series-level C-FIND.

        Args:
            query: Series query parameters

        Returns:
            DICOM dataset for query
        """
        ds = Dataset()
        ds.SpecificCharacterSet = _UTF8_CHARSET
        ds.QueryRetrieveLevel = QueryRetrieveLevel.SERIES.value
        ds.StudyInstanceUID = query.study_instance_uid

        _set_ds_fields(
            ds,
            {
                "SeriesInstanceUID": query.series_instance_uid,
                "SeriesNumber": query.series_number,
                "Modality": query.modality,
                "SeriesDescription": query.series_description,
                "NumberOfSeriesRelatedInstances": None,
                "BodyPartExamined": None,
                "ProtocolName": None,
                "SeriesDate": None,
                "OperatorsName": None,
                "PerformedProcedureStepDescription": None,
            },
        )

        return ds

    def _build_image_query_dataset(self, query: ImageQuery) -> Dataset:
        """Build DICOM dataset for image-level C-FIND.

        Args:
            query: Image query parameters

        Returns:
            DICOM dataset for query
        """
        ds = Dataset()
        ds.SpecificCharacterSet = _UTF8_CHARSET
        ds.QueryRetrieveLevel = QueryRetrieveLevel.IMAGE.value
        ds.StudyInstanceUID = query.study_instance_uid
        ds.SeriesInstanceUID = query.series_instance_uid

        _set_ds_fields(
            ds,
            {
                "SOPInstanceUID": query.sop_instance_uid,
                "InstanceNumber": query.instance_number,
                "SOPClassUID": None,
                "ImageType": None,
                "ContentDate": None,
                "SliceThickness": None,
            },
        )
        ds.Rows = None
        ds.Columns = None

        return ds

    def _build_retrieve_dataset(self, request: RetrieveRequest) -> Dataset:
        """Build DICOM dataset for C-MOVE.

        Args:
            request: Retrieve request parameters

        Returns:
            DICOM dataset for retrieve
        """
        ds = Dataset()
        data = request.to_dict()
        for key, value in data.items():
            setattr(ds, key, value)
        return ds

    def find_studies(self, config: AssociationConfig, query: StudyQuery) -> list[StudyResult]:
        """Execute C-FIND for studies.

        Args:
            config: Association configuration
            query: Study query parameters

        Returns:
            List of study results

        Raises:
            AssociationError: If association fails
        """
        ae = self._create_ae()
        ds = self._build_study_query_dataset(query)

        with self._association(ae, config) as assoc:
            results: list[StudyResult] = []
            responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)

            for status, identifier in responses:
                if not status:
                    continue

                # Pending status means we have data
                match status.Status:
                    case 0xFF00 | 0xFF01:
                        if identifier:
                            result = self._parse_study_result(identifier)
                            results.append(result)
                    case 0x0000:
                        logger.info(f"C-FIND completed successfully, found {len(results)} studies")
                    case _:
                        logger.warning(f"C-FIND warning status: 0x{status.Status:04x}")

            return results

    def find_series(self, config: AssociationConfig, query: SeriesQuery) -> list[SeriesResult]:
        """Execute C-FIND for series.

        Args:
            config: Association configuration
            query: Series query parameters

        Returns:
            List of series results

        Raises:
            AssociationError: If association fails
        """
        ae = self._create_ae()
        ds = self._build_series_query_dataset(query)

        with self._association(ae, config) as assoc:
            results: list[SeriesResult] = []
            responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)

            for status, identifier in responses:
                if not status:
                    continue

                match status.Status:
                    case 0xFF00 | 0xFF01:
                        if identifier:
                            result = self._parse_series_result(identifier)
                            results.append(result)
                    case 0x0000:
                        logger.info(f"C-FIND completed successfully, found {len(results)} series")

            return results

    def find_images(self, config: AssociationConfig, query: ImageQuery) -> list[ImageResult]:
        """Execute C-FIND for images.

        Args:
            config: Association configuration
            query: Image query parameters

        Returns:
            List of image results

        Raises:
            AssociationError: If association fails
        """
        ae = self._create_ae()
        ds = self._build_image_query_dataset(query)

        with self._association(ae, config) as assoc:
            results: list[ImageResult] = []
            responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)

            for status, identifier in responses:
                if not status:
                    continue

                match status.Status:
                    case 0xFF00 | 0xFF01:
                        if identifier:
                            result = self._parse_image_result(identifier)
                            results.append(result)
                    case 0x0000:
                        logger.info(f"C-FIND completed successfully, found {len(results)} images")

            return results

    def move_study(
        self, config: AssociationConfig, request: RetrieveRequest, destination_aet: str
    ) -> RetrieveResult:
        """Execute C-MOVE to move study to another node.

        Args:
            config: Association configuration
            request: Retrieve request
            destination_aet: Destination AE title

        Returns:
            Retrieve result

        Raises:
            AssociationError: If association fails
        """
        ae = self._create_ae()
        ds = self._build_retrieve_dataset(request)

        with self._association(ae, config) as assoc:
            result = RetrieveResult(status="pending")
            responses = assoc.send_c_move(
                ds, destination_aet, PatientRootQueryRetrieveInformationModelMove
            )

            for status, _identifier in responses:
                if not status:
                    continue

                # Update counters
                if hasattr(status, "NumberOfRemainingSuboperations"):
                    result.num_remaining = status.NumberOfRemainingSuboperations or 0
                if hasattr(status, "NumberOfCompletedSuboperations"):
                    result.num_completed = status.NumberOfCompletedSuboperations or 0
                if hasattr(status, "NumberOfFailedSuboperations"):
                    result.num_failed = status.NumberOfFailedSuboperations or 0
                if hasattr(status, "NumberOfWarningSuboperations"):
                    result.num_warning = status.NumberOfWarningSuboperations or 0

                match status.Status:
                    case 0x0000:
                        result.status = "success"
                        logger.info(
                            f"C-MOVE completed: {result.num_completed} completed, "
                            f"{result.num_failed} failed, destination: {destination_aet}"
                        )
                    case 0xFF00:
                        result.status = "pending"
                    case _:
                        result.status = f"warning_0x{status.Status:04x}"
                        logger.warning(f"C-MOVE status: 0x{status.Status:04x}")

            return result

    def retrieve_via_get(
        self,
        config: AssociationConfig,
        request: RetrieveRequest,
        storage: StorageConfig,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> RetrieveResult:
        """Retrieve instances via C-GET (same-association C-STORE sub-operations).

        Symmetric to retrieve_via_move but in-association: no StorageSCP, no pool.
        Handles STUDY and SERIES levels via request.level. MEMORY mode returns the
        instances in result.instances; DISK mode writes them under storage.output_dir.

        Note: in DISK mode instances are written to storage.output_dir and
        result.instances is left EMPTY — read them back from disk. Only MEMORY
        mode populates result.instances. This differs from retrieve_via_move, which
        populates result.instances in both modes.

        Raises:
            AssociationError: association fails, or the peer does not accept the
                C-GET presentation context.
        """
        ae, roles = self._create_get_ae()
        ds = self._build_retrieve_dataset(request)
        handlers, handler = create_store_handler(
            mode=storage.mode,
            output_dir=storage.output_dir,
            destination_aet=storage.destination_aet,
            destination_host=storage.destination_host,
            destination_port=storage.destination_port,
        )
        try:
            with self._association(ae, config, evt_handlers=handlers, ext_neg=roles) as assoc:
                result = RetrieveResult(status="pending")
                try:
                    responses = assoc.send_c_get(
                        ds, PatientRootQueryRetrieveInformationModelGet
                    )
                except ValueError as e:
                    raise AssociationError(
                        f"Peer did not accept the C-GET presentation context: {e}"
                    ) from e

                for status, _identifier in responses:
                    if not status:
                        continue
                    if hasattr(status, "NumberOfRemainingSuboperations"):
                        result.num_remaining = status.NumberOfRemainingSuboperations or 0
                    if hasattr(status, "NumberOfCompletedSuboperations"):
                        result.num_completed = status.NumberOfCompletedSuboperations or 0
                    if hasattr(status, "NumberOfFailedSuboperations"):
                        result.num_failed = status.NumberOfFailedSuboperations or 0
                    if hasattr(status, "NumberOfWarningSuboperations"):
                        result.num_warning = status.NumberOfWarningSuboperations or 0

                    if on_progress and result.num_completed > 0 and result.num_completed % 50 == 0:
                        total = result.num_completed + result.num_remaining
                        on_progress(result.num_completed, total)

                    match status.Status:
                        case 0x0000:
                            result.status = "success"
                            logger.info(
                                f"C-GET completed: {result.num_completed} completed, "
                                f"{result.num_failed} failed"
                            )
                        case 0xFF00:
                            result.status = "pending"
                        case _:
                            result.status = f"warning_0x{status.Status:04x}"
                            logger.warning(f"C-GET status: 0x{status.Status:04x}")

                if storage.mode == StorageMode.MEMORY:
                    result.instances = handler.get_stored_instances()
                return result
        finally:
            handler.close()

    def retrieve_via_move(
        self,
        config: AssociationConfig,
        request: RetrieveRequest,
        storage: StorageConfig,
        local_aet: str,
        scp: "StorageSCP",
        timeout: float = 300.0,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> RetrieveResult:
        """Retrieve DICOM instances via C-MOVE with local Storage SCP.

        Sends a C-MOVE request to the PACS, which connects back to our
        Storage SCP and delivers instances via C-STORE.

        Args:
            config: Association configuration for the PACS.
            request: Retrieve request (study/series level).
            storage: Storage configuration (mode used after reception).
            local_aet: Our AE title (C-MOVE destination).
            scp: Running StorageSCP instance to receive C-STORE.
            timeout: Seconds to wait for all instances to arrive.
            on_progress: Optional callback(completed, total) invoked every 50 instances.

        Returns:
            RetrieveResult with received instances.

        Raises:
            AssociationError: If association fails.
            RuntimeError: If SCP is not running.
        """
        if not scp.is_running:
            raise RuntimeError(
                "Storage SCP not running — C-MOVE retrieval requires a running Storage SCP "
                "to receive the moved instances; pass a started StorageSCP to retrieve_via_move."
            )

        # Derive session key
        series_uid = request.series_instance_uid or ""
        key = f"{request.study_instance_uid}/{series_uid}"

        scp.register_session(key)
        start_time = time.monotonic()

        try:
            ae = self._create_ae()
            ds = self._build_retrieve_dataset(request)

            with self._association(ae, config) as assoc:
                result = RetrieveResult(status="pending")
                responses = assoc.send_c_move(
                    ds, local_aet, PatientRootQueryRetrieveInformationModelMove
                )

                total_expected = None
                for status, _identifier in responses:
                    if not status:
                        continue

                    if hasattr(status, "NumberOfRemainingSuboperations"):
                        result.num_remaining = status.NumberOfRemainingSuboperations or 0
                    if hasattr(status, "NumberOfCompletedSuboperations"):
                        result.num_completed = status.NumberOfCompletedSuboperations or 0
                    if hasattr(status, "NumberOfFailedSuboperations"):
                        result.num_failed = status.NumberOfFailedSuboperations or 0
                    if hasattr(status, "NumberOfWarningSuboperations"):
                        result.num_warning = status.NumberOfWarningSuboperations or 0

                    if on_progress and result.num_completed > 0 and result.num_completed % 50 == 0:
                        total = result.num_completed + result.num_remaining
                        on_progress(result.num_completed, total)

                    # Compute expected count from first pending response
                    if total_expected is None and status.Status == 0xFF00:
                        total_expected = (
                            result.num_remaining
                            + result.num_completed
                            + result.num_failed
                            + result.num_warning
                        )
                        if total_expected > 0:
                            scp.set_expected(key, total_expected)

                    match status.Status:
                        case 0x0000:
                            result.status = "success"
                            logger.info(
                                f"C-MOVE completed: {result.num_completed} completed, "
                                f"{result.num_failed} failed"
                            )
                        case 0xFF00:
                            result.status = "pending"
                        case _:
                            result.status = f"warning_0x{status.Status:04x}"
                            logger.warning(f"C-MOVE status: 0x{status.Status:04x}")

            # Wait for SCP to receive all instances.
            # Fallback: if no pending response carried sub-operation counts,
            # `total_expected` is still None and SCP cannot detect completion.
            # Use the final response's completed count so the SCP can signal
            # `done` once all expected C-STOREs have arrived.
            if total_expected is None:
                scp.set_expected(key, result.num_completed)

            elapsed = time.monotonic() - start_time
            remaining = max(timeout - elapsed, 1.0)
            scp.wait_for_completion(key, timeout=remaining)

            # Collect results
            finished = scp.finish_session(key)
            if finished is None:
                return result

            result.instances = finished.instances
            result.num_completed = finished.received_count

            # Detect timeout: SCP didn't receive all expected instances
            if not finished.done.is_set():
                expected = finished.expected_count or "unknown"
                logger.warning(
                    f"C-MOVE timed out: received {finished.received_count}/{expected} "
                    f"instances in {timeout}s"
                )
                result.status = "timeout"

            # Write to disk if DISK mode requested
            if storage.mode == StorageMode.DISK and storage.output_dir:
                storage.output_dir.mkdir(parents=True, exist_ok=True)
                for sop_uid, instance_ds in finished.instances.items():
                    filepath = storage.output_dir / f"{sop_uid}.dcm"
                    instance_ds.save_as(filepath, enforce_file_format=True)

            logger.info(f"C-MOVE retrieve complete: {finished.received_count} instances received")
            return result

        except Exception:
            # Clean up session on any error
            scp.finish_session(key)
            raise

    def store_instance(self, config: AssociationConfig, dataset: Dataset) -> bool:
        """Send a single DICOM instance to a peer via C-STORE.

        Args:
            config: Association configuration
            dataset: DICOM dataset to send

        Returns:
            True if C-STORE succeeded

        Raises:
            AssociationError: If association fails
        """
        ae = AE(ae_title=self.calling_aet)
        ae.maximum_pdu_size = self.max_pdu

        # Add storage contexts for the dataset's SOP class
        for cx in StoragePresentationContexts:
            if cx.abstract_syntax is not None:
                ae.add_requested_context(cx.abstract_syntax)

        with self._association(ae, config) as assoc:
            status = assoc.send_c_store(dataset)
            if status and status.Status == 0x0000:
                logger.debug("C-STORE completed successfully")
                return True
            status_code = status.Status if status else "unknown"
            logger.warning(f"C-STORE failed with status: {status_code}")
            return False

    def store_instances_batch(
        self, config: AssociationConfig, datasets: list[Dataset]
    ) -> BatchStoreResult:
        """Send multiple DICOM instances via a single C-STORE association.

        Args:
            config: Association configuration
            datasets: DICOM datasets to send

        Returns:
            BatchStoreResult with counts and failed SOP UIDs

        Raises:
            AssociationError: If association fails
        """
        if not datasets:
            return BatchStoreResult()

        ae = AE(ae_title=self.calling_aet)
        ae.maximum_pdu_size = self.max_pdu

        for cx in StoragePresentationContexts:
            if cx.abstract_syntax is not None:
                ae.add_requested_context(cx.abstract_syntax)

        result = BatchStoreResult()
        with self._association(ae, config) as assoc:
            for ds in datasets:
                sop_uid = str(getattr(ds, "SOPInstanceUID", "unknown"))
                try:
                    status = assoc.send_c_store(ds)
                    if status and status.Status == 0x0000:
                        result.total_sent += 1
                    else:
                        status_code = status.Status if status else "unknown"
                        logger.warning(f"C-STORE failed for {sop_uid}: status {status_code}")
                        result.total_failed += 1
                        result.failed_sop_uids.append(sop_uid)
                except Exception:
                    logger.exception(f"C-STORE exception for {sop_uid}")
                    result.total_failed += 1
                    result.failed_sop_uids.append(sop_uid)

        logger.info(
            f"Batch C-STORE completed: {result.total_sent} sent, {result.total_failed} failed"
        )
        return result

    def _parse_study_result(self, ds: Dataset) -> StudyResult:
        """Parse DICOM dataset to StudyResult.

        Args:
            ds: DICOM dataset

        Returns:
            Parsed study result
        """
        ds = _pin_charset(ds)
        return StudyResult(
            patient_id=_ds_str(ds, "PatientID"),
            patient_name=_ds_str(ds, "PatientName"),
            study_instance_uid=str(ds.StudyInstanceUID),
            study_date=_ds_str(ds, "StudyDate"),
            study_time=_ds_str(ds, "StudyTime"),
            study_description=_ds_str(ds, "StudyDescription"),
            accession_number=_ds_str(ds, "AccessionNumber"),
            modalities_in_study=_ds_modalities(ds),
            number_of_study_related_series=_ds_int(ds, "NumberOfStudyRelatedSeries"),
            number_of_study_related_instances=_ds_int(ds, "NumberOfStudyRelatedInstances"),
            patient_birth_date=_ds_str(ds, "PatientBirthDate"),
            patient_sex=_ds_str(ds, "PatientSex"),
            study_id=_ds_str(ds, "StudyID"),
            referring_physician_name=_ds_str(ds, "ReferringPhysicianName"),
            institution_name=_ds_str(ds, "InstitutionName"),
            station_name=_ds_str(ds, "StationName"),
            sop_classes_in_study=_ds_str_list(ds, "SOPClassesInStudy"),
        )

    def _parse_series_result(self, ds: Dataset) -> SeriesResult:
        """Parse DICOM dataset to SeriesResult.

        Args:
            ds: DICOM dataset

        Returns:
            Parsed series result
        """
        ds = _pin_charset(ds)
        return SeriesResult(
            study_instance_uid=str(ds.StudyInstanceUID),
            series_instance_uid=str(ds.SeriesInstanceUID),
            series_number=_ds_int(ds, "SeriesNumber"),
            modality=_ds_str(ds, "Modality"),
            series_description=_ds_str(ds, "SeriesDescription"),
            number_of_series_related_instances=_ds_int(ds, "NumberOfSeriesRelatedInstances"),
            body_part_examined=_ds_str(ds, "BodyPartExamined"),
            protocol_name=_ds_str(ds, "ProtocolName"),
            series_date=_ds_str(ds, "SeriesDate"),
            operator_name=_ds_str_list(ds, "OperatorsName"),
            performed_procedure_step_description=_ds_str(
                ds, "PerformedProcedureStepDescription"
            ),
        )

    def _parse_image_result(self, ds: Dataset) -> ImageResult:
        """Parse DICOM dataset to ImageResult.

        Args:
            ds: DICOM dataset

        Returns:
            Parsed image result
        """
        ds = _pin_charset(ds)
        return ImageResult(
            study_instance_uid=str(ds.StudyInstanceUID),
            series_instance_uid=str(ds.SeriesInstanceUID),
            sop_instance_uid=str(ds.SOPInstanceUID),
            sop_class_uid=_ds_str(ds, "SOPClassUID"),
            instance_number=_ds_int(ds, "InstanceNumber"),
            rows=_ds_int(ds, "Rows"),
            columns=_ds_int(ds, "Columns"),
            image_type=_ds_str_list(ds, "ImageType"),
            content_date=_ds_str(ds, "ContentDate"),
            slice_thickness=_ds_float(ds, "SliceThickness"),
        )
