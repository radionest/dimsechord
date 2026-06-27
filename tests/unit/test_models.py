from pathlib import Path

from dimsechord._models import (
    MODALITIES_SEPARATOR,
    AssociationConfig,
    BatchStoreResult,
    DicomNode,
    QueryRetrieveLevel,
    RetrieveRequest,
    RetrieveResult,
    StorageConfig,
    StorageMode,
    StudyResult,
)


def test_modalities_separator_is_dicom_backslash() -> None:
    assert MODALITIES_SEPARATOR == "\\"


def test_retrieve_request_to_dict_skips_none() -> None:
    req = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid="1.2.3",
        series_instance_uid="1.2.3.4",
    )
    assert req.to_dict() == {
        "QueryRetrieveLevel": "SERIES",
        "StudyInstanceUID": "1.2.3",
        "SeriesInstanceUID": "1.2.3.4",
    }


def test_study_result_requires_study_uid_only() -> None:
    r = StudyResult(study_instance_uid="1.2.3")
    assert r.patient_id is None
    assert r.modalities_in_study is None


def test_storage_config_defaults() -> None:
    cfg = StorageConfig(mode=StorageMode.DISK, output_dir=Path("/tmp/x"))
    assert cfg.destination_aet is None


def test_association_config_defaults() -> None:
    cfg = AssociationConfig(calling_aet="ME", called_aet="PACS", peer_host="h", peer_port=104)
    assert cfg.max_pdu == 16384
    assert cfg.timeout == 30.0


def test_retrieve_result_counters_default_zero() -> None:
    res = RetrieveResult(status="pending")
    assert res.num_completed == 0
    assert res.failed_sop_instances == []
    assert res.instances == {}


def test_dicom_node() -> None:
    node = DicomNode(aet="PACS", host="1.2.3.4", port=104)
    assert (node.aet, node.host, node.port) == ("PACS", "1.2.3.4", 104)


def test_mutable_defaults_are_not_shared_between_instances() -> None:
    a = RetrieveResult(status="pending")
    b = RetrieveResult(status="pending")
    a.failed_sop_instances.append("1.2.3")
    a.instances["k"] = 1
    assert b.failed_sop_instances == []
    assert b.instances == {}

    x = BatchStoreResult()
    y = BatchStoreResult()
    x.failed_sop_uids.append("1.2.3")
    assert y.failed_sop_uids == []
