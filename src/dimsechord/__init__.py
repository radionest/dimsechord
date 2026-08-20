"""dimsechord — pure-Python DICOM + DICOMweb core.

This module is the **only** public surface. Everything else lives in
underscore-prefixed modules (``dimsechord._scu``, ``dimsechord._cache``, …)
and is private: importing from those modules is unsupported and may break
without notice. Keeping the public surface small keeps internal refactoring
free.

If something you need is not exported here, extend this surface (add it to a
private module and re-export it) rather than importing a private symbol.
"""

from dimsechord._bridge import iter_to_aiter
from dimsechord._cache import DicomCache, MemoryCachedSeries
from dimsechord._client import DicomClient
from dimsechord._converter import (
    DicomJson,
    TagValue,
    convert_datasets_to_dicom_json,
    dataset_to_dicom_json,
    dataset_to_qido_json,
    image_result_to_dicom_json,
    series_result_to_dicom_json,
    study_result_to_dicom_json,
)
from dimsechord._exceptions import (
    ArrivalTimeoutError,
    AssociationError,
    DimsechordError,
    FindFailedError,
    MoveToSelfError,
    NoPresentationContextError,
    PoolExhaustedError,
    RetrieveBusyError,
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
from dimsechord._presentation import (
    DEFAULT_COMPRESSED_TRANSFER_SYNTAXES,
    DEFAULT_IMAGE_STORAGE_CLASSES,
    DEFAULT_OTHER_STORAGE_CLASSES,
    build_storage_scp_contexts,
    build_storage_scu_contexts,
)
from dimsechord._pull_engine import PullEngine
from dimsechord._query_engine import QueryEngine
from dimsechord._scp import StorageSCP
from dimsechord._store_session import StoreSession

__version__ = "0.7.0"

# RUF022 wants ALL_CAPS constants grouped before CapWords classes (isort-style
# categorization); this project instead keeps __all__ in plain codepoint
# order (see test_all_is_sorted_and_unique), so the DEFAULT_* constants sit
# alphabetically among the classes rather than at the top.
__all__ = [  # noqa: RUF022
    "ArrivalTimeoutError",
    "AssociationError",
    "AssociationPool",
    "BatchStoreResult",
    "DEFAULT_COMPRESSED_TRANSFER_SYNTAXES",
    "DEFAULT_IMAGE_STORAGE_CLASSES",
    "DEFAULT_OTHER_STORAGE_CLASSES",
    "DicomCache",
    "DicomClient",
    "DicomJson",
    "DicomNode",
    "DimsechordError",
    "FindFailedError",
    "ImageQuery",
    "ImageResult",
    "MemoryCachedSeries",
    "MoveToSelfError",
    "NoPresentationContextError",
    "PoolExhaustedError",
    "PullEngine",
    "QueryEngine",
    "QueryRetrieveLevel",
    "RetrieveBusyError",
    "RetrieveResult",
    "SeriesQuery",
    "SeriesResult",
    "StorageSCP",
    "StoreSession",
    "StudyQuery",
    "StudyResult",
    "TagValue",
    "build_multipart_response",
    "build_storage_scp_contexts",
    "build_storage_scu_contexts",
    "convert_datasets_to_dicom_json",
    "dataset_to_dicom_json",
    "dataset_to_qido_json",
    "extract_frames_from_dataset",
    "image_result_to_dicom_json",
    "iter_to_aiter",
    "series_result_to_dicom_json",
    "study_result_to_dicom_json",
]
