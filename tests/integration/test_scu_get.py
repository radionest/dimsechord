import pytest
from pydicom.uid import JPEGLSLossless, generate_uid

from dimsechord._models import (
    AssociationConfig,
    QueryRetrieveLevel,
    RetrieveRequest,
    StorageConfig,
    StorageMode,
)
from dimsechord._presentation import (
    DEFAULT_IMAGE_STORAGE_CLASSES,
    DEFAULT_OTHER_STORAGE_CLASSES,
)
from dimsechord._scu import DicomOperations
from tests.factories import make_compressed_instance


def _config(fake_pacs) -> AssociationConfig:
    return AssociationConfig(
        calling_aet="GETSCU",
        called_aet=fake_pacs.aet,
        peer_host="127.0.0.1",
        peer_port=fake_pacs.port,
    )


@pytest.mark.timeout(60)
def test_retrieve_via_get_to_memory(fake_pacs, seeded_study) -> None:
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    ops = DicomOperations(calling_aet="GETSCU")
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=study,
        series_instance_uid=series,
    )
    result = ops.retrieve_via_get(
        _config(fake_pacs), request, StorageConfig(mode=StorageMode.MEMORY)
    )
    assert result.status == "success"
    assert result.num_completed == 2
    assert set(result.instances) == set(seeded_study[series])


@pytest.mark.timeout(60)
def test_retrieve_via_get_to_disk(fake_pacs, seeded_study, tmp_path) -> None:
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    out = tmp_path / "out"
    ops = DicomOperations(calling_aet="GETSCU")
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=study,
        series_instance_uid=series,
    )
    result = ops.retrieve_via_get(
        _config(fake_pacs), request, StorageConfig(mode=StorageMode.DISK, output_dir=out)
    )
    assert result.num_completed == 2
    assert {p.stem for p in out.glob("*.dcm")} == set(seeded_study[series])


def test_create_get_ae_negotiates_scp_role() -> None:
    ops = DicomOperations(calling_aet="GETSCU")
    _ae, roles = ops._create_get_ae()
    assert roles
    assert all(r.scp_role for r in roles)


@pytest.mark.timeout(60)
def test_retrieve_via_get_compressed_verbatim(fake_pacs, seeded_study) -> None:
    study = seeded_study["study"][0]
    series, sop = generate_uid(), generate_uid()
    fake_pacs.add_instance(make_compressed_instance(study, series, sop))
    ops = DicomOperations(calling_aet="GETSCU")
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=study,
        series_instance_uid=series,
    )
    result = ops.retrieve_via_get(
        _config(fake_pacs), request, StorageConfig(mode=StorageMode.MEMORY)
    )
    assert result.status == "success"
    assert result.num_completed == 1
    assert result.instances[sop].file_meta.TransferSyntaxUID == JPEGLSLossless


def test_create_get_ae_uses_curated_contexts_within_limit() -> None:
    ops = DicomOperations(calling_aet="GETSCU")
    ae, roles = ops._create_get_ae()
    # 2 GET models + 106 storage contexts, within the 128 limit
    assert len(ae.requested_contexts) == 108
    # one role per unique storage class, not per context
    expected_classes = set(DEFAULT_IMAGE_STORAGE_CLASSES) | set(DEFAULT_OTHER_STORAGE_CLASSES)
    assert {r.sop_class_uid for r in roles} == expected_classes
