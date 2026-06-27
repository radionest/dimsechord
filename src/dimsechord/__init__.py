"""dimsechord — neutral pure-Python DICOM + DICOMweb core.

This module is the **only** public surface. Everything else lives in
underscore-prefixed modules (``dimsechord._scu``, ``dimsechord._cache``, …)
and is private: importing from those modules is unsupported and may break
without notice. The library is consumed by several projects (``dicarina``,
``clarinet``); keeping the surface small keeps internal refactoring free.

If something you need is not exported here, extend this surface (add it to a
private module and re-export it) rather than importing a private symbol.
"""

from dimsechord._cache import DicomCache, MemoryCachedSeries
from dimsechord._client import DicomClient
from dimsechord._converter import (
    DicomJson,
    TagValue,
    convert_datasets_to_dicom_json,
    dataset_to_dicom_json,
    image_result_to_dicom_json,
    series_result_to_dicom_json,
    study_result_to_dicom_json,
)
from dimsechord._exceptions import (
    ArrivalTimeoutError,
    AssociationError,
    DimsechordError,
    MoveToSelfError,
    PoolExhaustedError,
)
from dimsechord._models import (
    BatchStoreResult,
    DicomNode,
    ImageQuery,
    ImageResult,
    QueryRetrieveLevel,
    RetrieveResult,
    SeriesQuery,
    SeriesResult,
    StudyQuery,
    StudyResult,
)
from dimsechord._multipart import build_multipart_response, extract_frames_from_dataset
from dimsechord._pool import AssociationPool
from dimsechord._pull_engine import PullEngine
from dimsechord._scp import StorageSCP

__version__ = "0.3.0"

__all__ = [
    "ArrivalTimeoutError",
    "AssociationError",
    "AssociationPool",
    "BatchStoreResult",
    "DicomCache",
    "DicomClient",
    "DicomJson",
    "DicomNode",
    "DimsechordError",
    "ImageQuery",
    "ImageResult",
    "MemoryCachedSeries",
    "MoveToSelfError",
    "PoolExhaustedError",
    "PullEngine",
    "QueryRetrieveLevel",
    "RetrieveResult",
    "SeriesQuery",
    "SeriesResult",
    "StorageSCP",
    "StudyQuery",
    "StudyResult",
    "TagValue",
    "build_multipart_response",
    "convert_datasets_to_dicom_json",
    "dataset_to_dicom_json",
    "extract_frames_from_dataset",
    "image_result_to_dicom_json",
    "series_result_to_dicom_json",
    "study_result_to_dicom_json",
]
