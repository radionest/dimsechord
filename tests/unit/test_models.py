from pathlib import Path

from dimsechord._models import (
    MODALITIES_SEPARATOR,
    AssociationConfig,
    BatchStoreResult,
    DicomNode,
    ImageResult,
    QueryRetrieveLevel,
    RetrieveRequest,
    RetrieveResult,
    SeriesResult,
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


def test_study_result_extended_fields_default_none() -> None:
    r = StudyResult(study_instance_uid="1.2.3")
    assert r.patient_birth_date is None
    assert r.patient_sex is None
    assert r.study_id is None
    assert r.referring_physician_name is None
    assert r.institution_name is None
    assert r.station_name is None
    assert r.sop_classes_in_study is None


def test_study_result_extended_fields_keyword_construction() -> None:
    r = StudyResult(
        study_instance_uid="1.2.3",
        patient_birth_date="19700101",
        patient_sex="M",
        study_id="S1",
        referring_physician_name="SMITH^JOHN",
        institution_name="HOSP",
        station_name="STN1",
        sop_classes_in_study=["1.2.840.10008.5.1.4.1.1.4"],
    )
    assert r.patient_birth_date == "19700101"
    assert r.sop_classes_in_study == ["1.2.840.10008.5.1.4.1.1.4"]


def test_series_result_extended_fields_default_none() -> None:
    r = SeriesResult(study_instance_uid="1.2.3", series_instance_uid="1.2.3.4")
    assert r.body_part_examined is None
    assert r.protocol_name is None
    assert r.series_date is None
    assert r.operator_name is None
    assert r.performed_procedure_step_description is None


def test_series_result_extended_fields_keyword_construction() -> None:
    r = SeriesResult(
        study_instance_uid="1.2.3",
        series_instance_uid="1.2.3.4",
        body_part_examined="BRAIN",
        protocol_name="PROT1",
        series_date="20200101",
        operator_name="OPER^X",
        performed_procedure_step_description="PPS desc",
    )
    assert r.body_part_examined == "BRAIN"
    assert r.operator_name == "OPER^X"


def test_image_result_extended_fields_default_none() -> None:
    r = ImageResult(
        study_instance_uid="1.2.3", series_instance_uid="1.2.3.4", sop_instance_uid="1.2.3.4.1"
    )
    assert r.image_type is None
    assert r.content_date is None
    assert r.slice_thickness is None


def test_image_result_extended_fields_keyword_construction() -> None:
    r = ImageResult(
        study_instance_uid="1.2.3",
        series_instance_uid="1.2.3.4",
        sop_instance_uid="1.2.3.4.1",
        image_type=["ORIGINAL", "PRIMARY"],
        content_date="20200101",
        slice_thickness=2.5,
    )
    assert r.image_type == ["ORIGINAL", "PRIMARY"]
    assert r.slice_thickness == 2.5
