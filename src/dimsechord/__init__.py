"""dimsechord — neutral pure-Python DICOM + DICOMweb core."""

from dimsechord.cache import DicomCache, MemoryCachedSeries
from dimsechord.client import DicomClient
from dimsechord.converter import (
    convert_datasets_to_dicom_json,
    dataset_to_dicom_json,
    image_result_to_dicom_json,
    series_result_to_dicom_json,
    study_result_to_dicom_json,
)
from dimsechord.exceptions import (
    ArrivalTimeoutError,
    AssociationError,
    DimsechordError,
    MoveToSelfError,
    PoolExhaustedError,
)
from dimsechord.handlers import StorageHandler, create_store_handler
from dimsechord.index import CacheIndex, IndexedInstance
from dimsechord.models import (
    AssociationConfig,
    BatchStoreResult,
    DicomNode,
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
from dimsechord.multipart import build_multipart_response, extract_frames_from_dataset
from dimsechord.pool import AssociationPool
from dimsechord.pull_engine import PullEngine
from dimsechord.scp import MoveSession, StorageSCP, get_storage_scp, shutdown_storage_scp
from dimsechord.scu import DicomOperations

__version__ = "0.1.0"

__all__ = [
    "ArrivalTimeoutError",
    "AssociationConfig",
    "AssociationError",
    "AssociationPool",
    "BatchStoreResult",
    "CacheIndex",
    "DicomCache",
    "DicomClient",
    "DicomNode",
    "DicomOperations",
    "DimsechordError",
    "ImageQuery",
    "ImageResult",
    "IndexedInstance",
    "MemoryCachedSeries",
    "MoveSession",
    "MoveToSelfError",
    "PoolExhaustedError",
    "PullEngine",
    "QueryRetrieveLevel",
    "RetrieveRequest",
    "RetrieveResult",
    "SeriesQuery",
    "SeriesResult",
    "StorageConfig",
    "StorageHandler",
    "StorageMode",
    "StorageSCP",
    "StudyQuery",
    "StudyResult",
    "build_multipart_response",
    "convert_datasets_to_dicom_json",
    "create_store_handler",
    "dataset_to_dicom_json",
    "extract_frames_from_dataset",
    "get_storage_scp",
    "image_result_to_dicom_json",
    "series_result_to_dicom_json",
    "shutdown_storage_scp",
    "study_result_to_dicom_json",
]
