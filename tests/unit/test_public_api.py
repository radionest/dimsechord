"""Freeze dimsechord's public surface.

The public contract is exactly ``dimsechord.__all__``. This test fails on any
accidental growth or shrinkage so the boundary cannot drift silently. Adding to
the surface is a deliberate act: update ``EXPECTED_PUBLIC`` in the same commit.
"""

import importlib
import inspect
import threading

import pytest

import dimsechord
from dimsechord import DicomClient
from dimsechord._scu import DicomOperations

# The complete, intended public surface. Keep sorted.
EXPECTED_PUBLIC = {
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
    "PoolExhaustedError",
    "PullEngine",
    "QueryEngine",
    "QueryRetrieveLevel",
    "RetrieveResult",
    "SeriesQuery",
    "SeriesResult",
    "StorageSCP",
    "StudyQuery",
    "StudyResult",
    "TagValue",
    "build_multipart_response",
    "build_storage_scu_contexts",
    "convert_datasets_to_dicom_json",
    "dataset_to_dicom_json",
    "dataset_to_qido_json",
    "extract_frames_from_dataset",
    "image_result_to_dicom_json",
    "iter_to_aiter",
    "series_result_to_dicom_json",
    "study_result_to_dicom_json",
}

# Symbols that were deliberately demoted to the private surface. They must stay
# out of the public namespace; promoting one is a conscious choice, not a slip.
MUST_STAY_PRIVATE = {
    "DicomOperations",
    "AssociationConfig",
    "RetrieveRequest",
    "StorageConfig",
    "StorageMode",
    "StorageHandler",
    "create_store_handler",
    "CacheIndex",
    "IndexedInstance",
    "MoveSession",
}


def test_all_matches_expected() -> None:
    assert set(dimsechord.__all__) == EXPECTED_PUBLIC


def test_all_is_sorted_and_unique() -> None:
    assert dimsechord.__all__ == sorted(dimsechord.__all__)
    assert len(dimsechord.__all__) == len(set(dimsechord.__all__))


def test_every_public_name_is_importable() -> None:
    for name in dimsechord.__all__:
        assert hasattr(dimsechord, name), f"{name} listed in __all__ but not importable"


def test_private_symbols_not_exported() -> None:
    leaked = MUST_STAY_PRIVATE & set(dimsechord.__all__)
    assert not leaked, f"private symbols leaked into the public surface: {sorted(leaked)}"
    assert not (MUST_STAY_PRIVATE & set(vars(dimsechord))), (
        "private symbol bound on the package namespace; import it from its "
        "underscore module instead of re-exporting it"
    )


def test_underscore_modules_are_importable_but_marked_private() -> None:
    # The implementation still lives in importable modules (tests use them), but
    # their underscore prefix is the signal that they are not the contract.
    mod = importlib.import_module("dimsechord._scu")
    assert mod.__name__ == "dimsechord._scu"


def test_set_max_concurrent_associations_is_classmethod() -> None:
    # Frozen public spelling on the façade; must be a classmethod (configures
    # process-global state, not per-instance).
    attr = DicomClient.__dict__["set_max_concurrent_associations"]
    assert type(attr) is classmethod


def test_set_max_concurrent_associations_installs_global_cap() -> None:
    # Delegates to the private DicomOperations semaphore. Save/restore the
    # process-global so this test cannot leak a cap into other tests.
    saved = DicomOperations._association_semaphore
    try:
        DicomClient.set_max_concurrent_associations(max_concurrent=2)
        sem = DicomOperations._association_semaphore
        assert isinstance(sem, threading.Semaphore)
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is True
        assert sem.acquire(blocking=False) is False  # cap of 2 reached
        sem.release()
        sem.release()
    finally:
        DicomOperations._association_semaphore = saved


def test_set_max_concurrent_associations_rejects_non_positive() -> None:
    # n < 1 must raise: n=0 would install Semaphore(0) and deadlock every
    # association. A rejected call must not mutate the process-global.
    saved = DicomOperations._association_semaphore
    try:
        for bad in (0, -1):
            with pytest.raises(ValueError):
                DicomClient.set_max_concurrent_associations(bad)
            assert DicomOperations._association_semaphore is saved
    finally:
        DicomOperations._association_semaphore = saved


def test_client_retrieve_methods_have_no_patient_id_parameter() -> None:
    retrieve_methods = [
        "move_study", "move_series", "get_study",
        "get_series", "get_study_to_memory", "get_series_to_memory",
    ]
    for name in retrieve_methods:
        params = inspect.signature(getattr(DicomClient, name)).parameters
        assert "patient_id" not in params, name


def test_client_retrieve_optional_parameters_are_keyword_only() -> None:
    """Removing patient_id shifted the positional slots: a legacy positional
    patient_id argument must fail with TypeError, not silently bind into the
    optional parameter that took its place."""
    retrieve_methods = [
        "move_study", "move_series", "get_study",
        "get_series", "get_study_to_memory", "get_series_to_memory",
    ]
    for name in retrieve_methods:
        params = inspect.signature(getattr(DicomClient, name)).parameters
        for param in params.values():
            if param.default is not inspect.Parameter.empty:
                assert param.kind is inspect.Parameter.KEYWORD_ONLY, (name, param.name)
