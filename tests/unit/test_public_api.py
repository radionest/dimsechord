"""Freeze dimsechord's public surface.

The package is consumed by several projects (dicarina, clarinet); the public
contract is exactly ``dimsechord.__all__``. This test fails on any accidental
growth or shrinkage so the boundary cannot drift silently. Adding to the
surface is a deliberate act: update ``EXPECTED_PUBLIC`` in the same commit.
"""

import importlib

import dimsechord

# The complete, intended public surface. Keep sorted.
EXPECTED_PUBLIC = {
    "ArrivalTimeoutError",
    "AssociationError",
    "AssociationPool",
    "BatchStoreResult",
    "DicomCache",
    "DicomClient",
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
    "build_multipart_response",
    "convert_datasets_to_dicom_json",
    "dataset_to_dicom_json",
    "extract_frames_from_dataset",
    "image_result_to_dicom_json",
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
