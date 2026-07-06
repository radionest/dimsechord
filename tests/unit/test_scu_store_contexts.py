"""Unit tests for dataset-driven C-STORE presentation contexts."""

import pytest
from pydicom.uid import ExplicitVRLittleEndian, JPEGLSLossless
from pynetdicom.presentation import DEFAULT_TRANSFER_SYNTAXES
from pynetdicom.sop_class import MRImageStorage  # type: ignore[attr-defined]

from dimsechord._scu import _storage_contexts_for_datasets
from tests.factories import make_compressed_instance, make_instance

STUDY, SERIES = "1.2.3", "1.2.3.4"


def test_uncompressed_dataset_gets_one_default_ts_context() -> None:
    ds = make_instance(STUDY, SERIES, "1.2.3.4.5")
    contexts = _storage_contexts_for_datasets([ds])
    assert len(contexts) == 1
    assert contexts[0].abstract_syntax == str(MRImageStorage)
    assert contexts[0].transfer_syntax == list(DEFAULT_TRANSFER_SYNTAXES)


def test_compressed_dataset_gets_exact_single_ts_context() -> None:
    ds = make_compressed_instance(STUDY, SERIES, "1.2.3.4.5")
    contexts = _storage_contexts_for_datasets([ds])
    assert len(contexts) == 1
    assert contexts[0].transfer_syntax == [str(JPEGLSLossless)]


def test_mixed_batch_dedupes_to_one_context_per_class_ts_pair() -> None:
    datasets = [
        make_instance(STUDY, SERIES, "1.1"),
        make_instance(STUDY, SERIES, "1.2"),
        make_compressed_instance(STUDY, SERIES, "1.3"),
        make_compressed_instance(STUDY, SERIES, "1.4"),
    ]
    contexts = _storage_contexts_for_datasets(datasets)
    assert len(contexts) == 2  # one uncompressed MR + one JPEG-LS MR


def test_dataset_without_file_meta_falls_back_to_default_ts() -> None:
    ds = make_instance(STUDY, SERIES, "1.1")
    del ds.file_meta
    contexts = _storage_contexts_for_datasets([ds])
    assert len(contexts) == 1
    assert contexts[0].transfer_syntax == list(DEFAULT_TRANSFER_SYNTAXES)


def test_explicit_vr_counts_as_uncompressed() -> None:
    ds = make_instance(STUDY, SERIES, "1.1")
    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    contexts = _storage_contexts_for_datasets([ds])
    assert contexts[0].transfer_syntax == list(DEFAULT_TRANSFER_SYNTAXES)


def test_more_than_128_unique_pairs_raises() -> None:
    datasets = []
    for i in range(129):
        ds = make_instance(STUDY, SERIES, f"1.{i}")
        ds.SOPClassUID = f"1.2.840.99.{i}"
        datasets.append(ds)
    with pytest.raises(ValueError, match="128"):
        _storage_contexts_for_datasets(datasets)
