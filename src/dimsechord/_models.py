"""Pydantic models for DICOM operations.

Only a subset of these models is re-exported from ``dimsechord/__init__.py`` (the
queries, results, ``DicomNode``, ``QueryRetrieveLevel``). The rest —
``AssociationConfig``, ``RetrieveRequest``, ``StorageConfig``, ``StorageMode`` —
are INTERNAL: they exist to drive the private SCU / handlers. Before importing one
into a consumer, prefer the public façade (``DicomClient`` / ``PullEngine``), or
extend the public surface, instead of depending on these private types.
"""

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

#: DICOM PS3.5 §6.4 value-multiplicity separator used to join multi-valued
#: ``ModalitiesInStudy`` into a single byte-identical-to-wire string.
MODALITIES_SEPARATOR = "\\"


class QueryRetrieveLevel(StrEnum):
    """DICOM Query/Retrieve levels."""

    PATIENT = "PATIENT"
    STUDY = "STUDY"
    SERIES = "SERIES"
    IMAGE = "IMAGE"


class StudyQuery(BaseModel):
    """Query parameters for C-FIND at study level."""

    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str | None = None
    study_date: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    modality: str | None = None


class SeriesQuery(BaseModel):
    """Query parameters for C-FIND at series level."""

    study_instance_uid: str
    series_instance_uid: str | None = None
    series_number: str | None = None
    modality: str | None = None
    series_description: str | None = None


class ImageQuery(BaseModel):
    """Query parameters for C-FIND at image level."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str | None = None
    instance_number: str | None = None


class StudyResult(BaseModel):
    """Study-level C-FIND result."""

    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str
    study_date: str | None = None
    study_time: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    modalities_in_study: str | None = Field(
        default=None,
        description="Modalities of the study, DICOM-standard '\\'-joined (e.g. 'CT\\SR').",
    )
    number_of_study_related_series: int | None = None
    number_of_study_related_instances: int | None = None


class SeriesResult(BaseModel):
    """Series-level C-FIND result."""

    study_instance_uid: str
    series_instance_uid: str
    series_number: int | None = None
    modality: str | None = None
    series_description: str | None = None
    number_of_series_related_instances: int | None = None


class ImageResult(BaseModel):
    """Image-level C-FIND result."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str
    sop_class_uid: str | None = None
    instance_number: int | None = None
    rows: int | None = None
    columns: int | None = None


class RetrieveRequest(BaseModel):
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


class StorageConfig(BaseModel):
    """Configuration for storage handler."""

    mode: StorageMode
    output_dir: Path | None = None
    destination_aet: str | None = None
    destination_host: str | None = None
    destination_port: int | None = None


class RetrieveResult(BaseModel):
    """Result of a C-MOVE operation."""

    status: str
    num_remaining: int = 0
    num_completed: int = 0
    num_failed: int = 0
    num_warning: int = 0
    failed_sop_instances: list[str] = Field(default_factory=list)
    instances: dict[str, Any] = Field(default_factory=dict)


class BatchStoreResult(BaseModel):
    """Result of a batch C-STORE operation (one association, multiple datasets)."""

    total_sent: int = 0
    total_failed: int = 0
    failed_sop_uids: list[str] = Field(default_factory=list)


class DicomNode(BaseModel):
    """DICOM node configuration."""

    aet: str
    host: str
    port: int


class AssociationConfig(BaseModel):
    """Configuration for a DICOM association."""

    calling_aet: str
    called_aet: str
    peer_host: str
    peer_port: int
    max_pdu: int = 16384
    timeout: float = 30.0
