"""Dataclass models for DICOM operations.

Only a subset of these models is re-exported from ``dimsechord/__init__.py`` (the
queries, results, ``DicomNode``, ``QueryRetrieveLevel``). The rest —
``AssociationConfig``, ``RetrieveRequest``, ``StorageConfig``, ``StorageMode`` —
are INTERNAL: they exist to drive the private SCU / handlers. Before importing one
into a consumer, prefer the public façade (``DicomClient`` / ``PullEngine``), or
extend the public surface, instead of depending on these private types.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

#: DICOM PS3.5 §6.4 value-multiplicity separator used to join multi-valued
#: ``ModalitiesInStudy`` into a single byte-identical-to-wire string.
MODALITIES_SEPARATOR = "\\"


class QueryRetrieveLevel(StrEnum):
    """DICOM Query/Retrieve levels."""

    PATIENT = "PATIENT"
    STUDY = "STUDY"
    SERIES = "SERIES"
    IMAGE = "IMAGE"


@dataclass(slots=True, kw_only=True)
class StudyQuery:
    """Query parameters for C-FIND at study level."""

    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str | None = None
    study_date: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    modality: str | None = None


@dataclass(slots=True, kw_only=True)
class SeriesQuery:
    """Query parameters for C-FIND at series level."""

    study_instance_uid: str
    series_instance_uid: str | None = None
    series_number: str | None = None
    modality: str | None = None
    series_description: str | None = None


@dataclass(slots=True, kw_only=True)
class ImageQuery:
    """Query parameters for C-FIND at image level."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str | None = None
    instance_number: str | None = None


@dataclass(slots=True, kw_only=True)
class StudyResult:
    """Study-level C-FIND result."""

    study_instance_uid: str
    patient_id: str | None = None
    patient_name: str | None = None
    study_date: str | None = None
    study_time: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    # Modalities of the study, DICOM-standard '\'-joined (e.g. 'CT\SR').
    modalities_in_study: str | None = None
    number_of_study_related_series: int | None = None
    number_of_study_related_instances: int | None = None
    # Standard study-level fields (C-FIND result extension).
    patient_birth_date: str | None = None
    patient_sex: str | None = None
    study_id: str | None = None
    referring_physician_name: str | None = None
    institution_name: str | None = None
    station_name: str | None = None
    sop_classes_in_study: list[str] | None = None


@dataclass(slots=True, kw_only=True)
class SeriesResult:
    """Series-level C-FIND result."""

    study_instance_uid: str
    series_instance_uid: str
    series_number: int | None = None
    modality: str | None = None
    series_description: str | None = None
    number_of_series_related_instances: int | None = None
    # Standard series-level fields (C-FIND result extension).
    body_part_examined: str | None = None
    protocol_name: str | None = None
    series_date: str | None = None
    operator_name: list[str] | None = None
    performed_procedure_step_description: str | None = None


@dataclass(slots=True, kw_only=True)
class ImageResult:
    """Image-level C-FIND result."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str
    sop_class_uid: str | None = None
    instance_number: int | None = None
    rows: int | None = None
    columns: int | None = None
    # Standard image-level fields (C-FIND result extension).
    image_type: list[str] | None = None
    content_date: str | None = None
    slice_thickness: float | None = None


@dataclass(slots=True, kw_only=True)
class RetrieveRequest:
    """Request for a C-MOVE operation."""

    level: QueryRetrieveLevel
    patient_id: str | None = None
    study_instance_uid: str | None = None
    series_instance_uid: str | None = None
    sop_instance_uid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for dataset creation."""
        data: dict[str, Any] = {"QueryRetrieveLevel": self.level.value}
        if self.patient_id:
            data["PatientID"] = self.patient_id
        if self.study_instance_uid:
            data["StudyInstanceUID"] = self.study_instance_uid
        if self.series_instance_uid:
            data["SeriesInstanceUID"] = self.series_instance_uid
        if self.sop_instance_uid:
            data["SOPInstanceUID"] = self.sop_instance_uid
        return data


class StorageMode(StrEnum):
    """Storage modes for received DICOM instances."""

    DISK = "disk"
    MEMORY = "memory"
    FORWARD = "forward"


@dataclass(slots=True, kw_only=True)
class StorageConfig:
    """Configuration for storage handler."""

    mode: StorageMode
    output_dir: Path | None = None
    destination_aet: str | None = None
    destination_host: str | None = None
    destination_port: int | None = None


@dataclass(slots=True, kw_only=True)
class RetrieveResult:
    """Result of a C-MOVE operation."""

    status: str
    num_remaining: int = 0
    num_completed: int = 0
    num_failed: int = 0
    num_warning: int = 0
    failed_sop_instances: list[str] = field(default_factory=list)
    instances: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class BatchStoreResult:
    """Result of a batch C-STORE operation (one association, multiple datasets)."""

    total_sent: int = 0
    total_failed: int = 0
    failed_sop_uids: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class DicomNode:
    """DICOM node configuration."""

    aet: str
    host: str
    port: int


@dataclass(slots=True, kw_only=True)
class AssociationConfig:
    """Configuration for a DICOM association."""

    calling_aet: str
    called_aet: str
    peer_host: str
    peer_port: int
    max_pdu: int = 16384
    timeout: float = 30.0
