"""Unit tests for the storage SCU presentation-context builder."""

import pytest
from pynetdicom.presentation import DEFAULT_TRANSFER_SYNTAXES, build_context
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    CTImageStorage,
    MRImageStorage,
)

from dimsechord._presentation import (
    DEFAULT_COMPRESSED_TRANSFER_SYNTAXES,
    DEFAULT_IMAGE_STORAGE_CLASSES,
    DEFAULT_OTHER_STORAGE_CLASSES,
    build_storage_scp_contexts,
    build_storage_scu_contexts,
    matches_accepted_context,
)

JPEG_LS_LOSSLESS = "1.2.840.10008.1.2.4.80"

_CT = "1.2.840.10008.5.1.4.1.1.2"
_IMPLICIT, _EXPLICIT = "1.2.840.10008.1.2", "1.2.840.10008.1.2.1"
_BIG_ENDIAN, _JPEG_LS = "1.2.840.10008.1.2.2", "1.2.840.10008.1.2.4.80"


def test_default_tuple_sizes() -> None:
    assert len(DEFAULT_COMPRESSED_TRANSFER_SYNTAXES) == 8
    assert len(DEFAULT_IMAGE_STORAGE_CLASSES) == 10
    assert len(DEFAULT_OTHER_STORAGE_CLASSES) == 16


def test_default_context_count_is_106_and_fits_the_limit() -> None:
    contexts = build_storage_scu_contexts()
    assert len(contexts) == 106
    assert len(contexts) <= 128


def test_image_class_gets_uncompressed_plus_one_context_per_compressed_ts() -> None:
    contexts = build_storage_scu_contexts()
    ct = [c for c in contexts if c.abstract_syntax == str(CTImageStorage)]
    assert len(ct) == 1 + len(DEFAULT_COMPRESSED_TRANSFER_SYNTAXES)
    # First CT context carries the uncompressed defaults…
    assert ct[0].transfer_syntax == list(DEFAULT_TRANSFER_SYNTAXES)
    # …and every remaining CT context carries exactly one compressed TS.
    single = [c.transfer_syntax for c in ct[1:]]
    assert all(len(ts) == 1 for ts in single)
    assert {ts[0] for ts in single} == set(DEFAULT_COMPRESSED_TRANSFER_SYNTAXES)


def test_every_compressed_context_has_exactly_one_transfer_syntax() -> None:
    for ctx in build_storage_scu_contexts():
        if ctx.transfer_syntax != list(DEFAULT_TRANSFER_SYNTAXES):
            assert len(ctx.transfer_syntax) == 1
            assert ctx.transfer_syntax[0] in DEFAULT_COMPRESSED_TRANSFER_SYNTAXES


def test_other_classes_are_uncompressed_only() -> None:
    contexts = build_storage_scu_contexts()
    for cls in DEFAULT_OTHER_STORAGE_CLASSES:
        matching = [c for c in contexts if c.abstract_syntax == str(cls)]
        assert len(matching) == 1
        assert matching[0].transfer_syntax == list(DEFAULT_TRANSFER_SYNTAXES)


def test_custom_lists_are_respected() -> None:
    contexts = build_storage_scu_contexts(
        image_classes=[MRImageStorage],
        compressed_syntaxes=[JPEG_LS_LOSSLESS],
        other_classes=[],
    )
    assert len(contexts) == 2
    assert contexts[0].abstract_syntax == str(MRImageStorage)
    assert contexts[0].transfer_syntax == list(DEFAULT_TRANSFER_SYNTAXES)
    assert contexts[1].transfer_syntax == [JPEG_LS_LOSSLESS]


def test_overflow_raises_value_error() -> None:
    # 15 image classes x (1 + 8) = 135 > 128
    classes = [f"1.2.3.{i}" for i in range(15)]
    with pytest.raises(ValueError, match="128"):
        build_storage_scu_contexts(image_classes=classes, other_classes=[])


def test_max_contexts_reserve_for_cget() -> None:
    # Defaults fit a 126 budget (2 slots reserved for the GET models)…
    assert len(build_storage_scu_contexts(max_contexts=126)) == 106
    # …and a tighter budget rejects them.
    with pytest.raises(ValueError, match="105"):
        build_storage_scu_contexts(max_contexts=105)


def test_scp_contexts_image_classes_accept_compressed() -> None:
    by_class = {c.abstract_syntax: c for c in build_storage_scp_contexts()}
    assert len(by_class) == len(DEFAULT_IMAGE_STORAGE_CLASSES) + len(DEFAULT_OTHER_STORAGE_CLASSES)
    for cls in DEFAULT_IMAGE_STORAGE_CLASSES:
        ts = set(by_class[cls].transfer_syntax)
        assert set(DEFAULT_COMPRESSED_TRANSFER_SYNTAXES) <= ts
        assert set(DEFAULT_TRANSFER_SYNTAXES) <= ts


def test_scp_contexts_other_classes_uncompressed_only() -> None:
    by_class = {c.abstract_syntax: c for c in build_storage_scp_contexts()}
    for cls in DEFAULT_OTHER_STORAGE_CLASSES:
        assert set(by_class[cls].transfer_syntax) == set(DEFAULT_TRANSFER_SYNTAXES)


def test_scp_accept_set_is_subset_of_scu_propose_set() -> None:
    scu_pairs = {
        (c.abstract_syntax, ts)
        for c in build_storage_scu_contexts()
        for ts in c.transfer_syntax
    }
    scp_pairs = {
        (c.abstract_syntax, ts)
        for c in build_storage_scp_contexts()
        for ts in c.transfer_syntax
    }
    assert scp_pairs <= scu_pairs


@pytest.mark.parametrize(
    ("accepted_ts", "dataset_ts", "expected"),
    [
        (_EXPLICIT, _EXPLICIT, True),  # exact uncompressed
        (_JPEG_LS, _JPEG_LS, True),  # exact compressed
        (_IMPLICIT, _EXPLICIT, True),  # implicit<->explicit conversion
        (_IMPLICIT, _BIG_ENDIAN, False),  # endianness mismatch never converts
        (_EXPLICIT, _JPEG_LS, False),  # compressed never converts
        (_JPEG_LS, _EXPLICIT, False),  # ...in either direction
    ],
)
def test_matches_accepted_context(accepted_ts, dataset_ts, expected) -> None:
    cx = build_context(_CT, [accepted_ts])
    assert matches_accepted_context([cx], _CT, dataset_ts) is expected
    assert not matches_accepted_context([cx], "1.2.840.10008.5.1.4.1.1.4", _EXPLICIT)
