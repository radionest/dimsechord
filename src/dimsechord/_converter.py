"""DICOM JSON conversion for DICOMweb responses.

Converts result dataclasses and pydicom Datasets into DICOM JSON format
as defined by the DICOMweb standard (PS3.18 Appendix F).
"""

import logging
from collections.abc import Iterable
from typing import Any, NotRequired, TypedDict, cast

from pydicom import Dataset
from pydicom.dataelem import DataElement

from dimsechord._models import (
    MODALITIES_SEPARATOR,
    ImageResult,
    SeriesResult,
    StudyResult,
)


class TagValue(TypedDict):
    """One DICOM-JSON tag value (PS3.18 Annex F): a VR plus its payload."""

    vr: str
    Value: NotRequired[list[Any]]
    BulkDataURI: NotRequired[str]
    InlineBinary: NotRequired[str]


#: DICOM-JSON object: 8-hex-digit tag → its VR-tagged value.
DicomJson = dict[str, TagValue]

logger = logging.getLogger(__name__)


def _tag_value(vr: str, value: Any) -> TagValue:
    entry: TagValue = {"vr": vr}
    if value is not None:
        entry["Value"] = value if isinstance(value, list) else [value]
    return entry


def _fields_to_dicom_json(fields: list[tuple[str, str, Any]]) -> DicomJson:
    return {tag: _tag_value(vr, val) for tag, vr, val in fields if val is not None}


def _modalities_to_list(raw: str | None) -> list[str] | None:
    """Split DB-stored DICOM ``\\``-joined modalities back into a list.

    The DB column stores the DICOM PS3.5 wire form (``"CT\\SR"``); DICOM
    JSON (PS3.18 Appendix F) requires multi-value CS tags to be a JSON
    array of strings (``["CT", "SR"]``), not a single joined token.
    """
    if not raw:
        return None
    parts = [p for p in raw.split(MODALITIES_SEPARATOR) if p]
    return parts or None


def study_result_to_dicom_json(result: StudyResult) -> DicomJson:
    return _fields_to_dicom_json(
        [
            ("0020000D", "UI", result.study_instance_uid),
            ("00100020", "LO", result.patient_id),
            (
                "00100010",
                "PN",
                {"Alphabetic": result.patient_name} if result.patient_name else None,
            ),
            ("00080020", "DA", result.study_date),
            ("00080030", "TM", result.study_time),
            ("00081030", "LO", result.study_description),
            ("00080050", "SH", result.accession_number),
            ("00080061", "CS", _modalities_to_list(result.modalities_in_study)),
            ("00201206", "IS", result.number_of_study_related_series),
            ("00201208", "IS", result.number_of_study_related_instances),
        ]
    )


def series_result_to_dicom_json(result: SeriesResult) -> DicomJson:
    return _fields_to_dicom_json(
        [
            ("0020000D", "UI", result.study_instance_uid),
            ("0020000E", "UI", result.series_instance_uid),
            ("00080060", "CS", result.modality),
            ("00200011", "IS", result.series_number),
            ("0008103E", "LO", result.series_description),
            ("00201209", "IS", result.number_of_series_related_instances),
        ]
    )


def image_result_to_dicom_json(result: ImageResult) -> DicomJson:
    return _fields_to_dicom_json(
        [
            ("0020000D", "UI", result.study_instance_uid),
            ("0020000E", "UI", result.series_instance_uid),
            ("00080018", "UI", result.sop_instance_uid),
            ("00080016", "UI", result.sop_class_uid),
            ("00200013", "IS", result.instance_number),
            ("00280010", "US", result.rows),
            ("00280011", "US", result.columns),
        ]
    )


def _skip_bulk_data(_data_element: DataElement) -> str:
    """Bulk data handler that skips encoding of large binary elements like PixelData.

    Passed to each ``DataElement.to_json_dict`` call so that PixelData is never
    base64-encoded, avoiding the need to copy or mutate the original dataset.

    Args:
        _data_element: The pydicom data element being serialized (unused, required by API)

    Returns:
        Empty string placeholder (the tag is replaced with BulkDataURI afterward)
    """
    return ""


def dataset_to_dicom_json(ds: Dataset, base_url: str) -> DicomJson:
    """Convert a pydicom Dataset to DICOM JSON, replacing PixelData with BulkDataURI.

    The original dataset is **never mutated** — PixelData is skipped during JSON
    serialization via a bulk data handler, then replaced with a BulkDataURI entry.

    Args:
        ds: pydicom Dataset (may contain PixelData)
        base_url: Base URL for constructing BulkDataURIs

    Returns:
        DICOM JSON dict keyed by tag
    """
    # Replicates Dataset.to_json_dict(suppress_invalid_tags=True) WITHOUT its
    # config.strict_reading() wrapper: that context manager mutates the GLOBAL
    # pydicom validation mode, so concurrent conversions in asyncio.to_thread
    # (multi-study preload) race on it and can leave RAISE mode set process-wide.
    # Lenient per-tag serialization normalizes sloppy-but-convertible values
    # (e.g. IS '606.0000000000' → 606); truly unconvertible tags are skipped.
    sop_uid = ds.get("SOPInstanceUID", "unknown")
    json_dict: dict[str, Any] = {}
    # SIM118 is a false positive: iterating a Dataset directly yields DataElements
    # (converting raw values OUTSIDE the try below), not keys.
    for key in ds.keys():  # noqa: SIM118
        json_key = f"{key:08X}"
        try:
            json_dict[json_key] = ds[key].to_json_dict(
                bulk_data_element_handler=_skip_bulk_data,
                bulk_data_threshold=1024,
            )
        except Exception as e:
            logger.warning(f"Skipping non-serializable tag {json_key} in instance {sop_uid}: {e}")

    # Always set BulkDataURI for pixel data retrieval — even when PixelData
    # was stripped from the dataset before conversion (metadata endpoint) or
    # when pydicom omits large binary elements via bulk_data_threshold.
    study_uid = str(ds.get("StudyInstanceUID", ""))
    series_uid = str(ds.get("SeriesInstanceUID", ""))
    instance_uid = str(ds.get("SOPInstanceUID", ""))

    json_dict.pop("7FE00010", None)
    if instance_uid:
        json_dict["7FE00010"] = {
            "vr": "OW",
            "BulkDataURI": (
                f"{base_url}/studies/{study_uid}/series/{series_uid}"
                f"/instances/{instance_uid}/frames/1"
            ),
        }

    return cast("DicomJson", json_dict)


def convert_datasets_to_dicom_json(datasets: Iterable[Dataset], base_url: str) -> list[DicomJson]:
    metadata: list[DicomJson] = []
    for ds in datasets:
        try:
            metadata.append(dataset_to_dicom_json(ds, base_url))
        except Exception as e:
            sop_uid = getattr(ds, "SOPInstanceUID", "unknown")
            logger.warning(f"Skipping unreadable instance {sop_uid}: {e}")
    return metadata
