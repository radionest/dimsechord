import pytest
from pydicom import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    StudyRootQueryRetrieveInformationModelFind,
    Verification,
)

from dimsechord._client import DicomClient
from dimsechord._models import AssociationConfig, DicomNode, ImageQuery, SeriesQuery, StudyQuery
from dimsechord._scu import DicomOperations
from tests.factories import make_instance
from tests.fake_pacs import FakePacs


@pytest.mark.timeout(30)
def test_fake_pacs_answers_echo_and_find(fake_pacs: FakePacs, seeded_study) -> None:
    ae = AE(ae_title="TESTSCU")
    ae.add_requested_context(Verification)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = ae.associate("127.0.0.1", fake_pacs.port, ae_title=fake_pacs.aet)  # type: ignore[attr-defined]
    assert assoc.is_established
    try:
        echo = assoc.send_c_echo()
        assert echo.Status == 0x0000

        q = Dataset()
        q.QueryRetrieveLevel = "STUDY"
        q.StudyInstanceUID = ""
        studies = [
            ident
            for status, ident in assoc.send_c_find(
                q, StudyRootQueryRetrieveInformationModelFind
            )
            if status and status.Status in (0xFF00, 0xFF01) and ident is not None
        ]
        assert len(studies) == 1
        assert str(studies[0].StudyInstanceUID) == seeded_study["study"][0]
    finally:
        assoc.release()


def _config(pacs) -> AssociationConfig:
    return AssociationConfig(
        calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=pacs.port
    )


@pytest.mark.timeout(30)
def test_scu_find_studies_and_series(fake_pacs, seeded_study) -> None:
    ops = DicomOperations(calling_aet="TESTSCU")
    studies = ops.find_studies(_config(fake_pacs), StudyQuery())
    assert len(studies) == 1
    assert studies[0].study_instance_uid == seeded_study["study"][0]
    assert studies[0].number_of_study_related_series == 2

    series = ops.find_series(
        _config(fake_pacs), SeriesQuery(study_instance_uid=seeded_study["study"][0])
    )
    assert {s.series_instance_uid for s in series} == set(seeded_study["series"])


@pytest.mark.timeout(30)
def test_query_dataset_pins_utf8_charset() -> None:
    ops = DicomOperations(calling_aet="X")
    ds = ops._build_study_query_dataset(StudyQuery(patient_name="ИВАНОВ"))
    assert ds.SpecificCharacterSet == "ISO_IR 192"


@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_async_client_find_studies(fake_pacs, seeded_study) -> None:
    client = DicomClient(calling_aet="ASYNCSCU")
    peer = DicomNode(aet=fake_pacs.aet, host="127.0.0.1", port=fake_pacs.port)
    studies = await client.find_studies(StudyQuery(), peer)
    assert len(studies) == 1
    assert studies[0].study_instance_uid == seeded_study["study"][0]


@pytest.mark.timeout(30)
def test_scu_find_round_trips_extended_study_fields(free_port) -> None:
    pacs = FakePacs(aet="FAKEPACS")
    ds = make_instance("1.2.3", "1.2.3.4", "1.2.3.4.1")
    ds.PatientBirthDate = "19700101"
    ds.PatientSex = "M"
    ds.StudyID = "S1"
    ds.ReferringPhysicianName = "SMITH^JOHN"
    ds.InstitutionName = "HOSP"
    ds.StationName = "STN1"
    ds.SOPClassesInStudy = ["1.2.840.10008.5.1.4.1.1.4"]
    pacs.add_instance(ds)
    port = free_port()
    pacs.start(port)
    pacs.port = port
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        cfg = AssociationConfig(
            calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=port
        )
        studies = ops.find_studies(cfg, StudyQuery())
        assert len(studies) == 1
        s = studies[0]
        assert s.patient_birth_date == "19700101"
        assert s.patient_sex == "M"
        assert s.study_id == "S1"
        assert s.referring_physician_name == "SMITH^JOHN"
        assert s.institution_name == "HOSP"
        assert s.station_name == "STN1"
        assert s.sop_classes_in_study == ["1.2.840.10008.5.1.4.1.1.4"]
    finally:
        pacs.stop()


@pytest.mark.timeout(30)
def test_scu_find_round_trips_extended_series_fields(free_port) -> None:
    pacs = FakePacs(aet="FAKEPACS")
    ds = make_instance("1.2.3", "1.2.3.4", "1.2.3.4.1")
    ds.BodyPartExamined = "BRAIN"
    ds.ProtocolName = "PROT1"
    ds.SeriesDate = "20200101"
    ds.OperatorsName = ["OPER^X", "OPER^Y"]
    ds.PerformedProcedureStepDescription = "PPS desc"
    pacs.add_instance(ds)
    port = free_port()
    pacs.start(port)
    pacs.port = port
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        cfg = AssociationConfig(
            calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=port
        )
        series = ops.find_series(cfg, SeriesQuery(study_instance_uid="1.2.3"))
        assert len(series) == 1
        sr = series[0]
        assert sr.body_part_examined == "BRAIN"
        assert sr.protocol_name == "PROT1"
        assert sr.series_date == "20200101"
        assert sr.operator_name == ["OPER^X", "OPER^Y"]
        assert sr.performed_procedure_step_description == "PPS desc"
    finally:
        pacs.stop()


@pytest.mark.timeout(30)
def test_scu_find_round_trips_single_operator_name(free_port) -> None:
    pacs = FakePacs(aet="FAKEPACS")
    ds = make_instance("1.2.3", "1.2.3.4", "1.2.3.4.1")
    ds.OperatorsName = "OPER^X"
    pacs.add_instance(ds)
    port = free_port()
    pacs.start(port)
    pacs.port = port
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        cfg = AssociationConfig(
            calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=port
        )
        series = ops.find_series(cfg, SeriesQuery(study_instance_uid="1.2.3"))
        assert len(series) == 1
        assert series[0].operator_name == ["OPER^X"]
    finally:
        pacs.stop()


@pytest.mark.timeout(30)
def test_scu_find_round_trips_extended_image_fields(free_port) -> None:
    pacs = FakePacs(aet="FAKEPACS")
    ds = make_instance("1.2.3", "1.2.3.4", "1.2.3.4.1")
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    ds.ContentDate = "20200101"
    ds.SliceThickness = 2.5
    pacs.add_instance(ds)
    port = free_port()
    pacs.start(port)
    pacs.port = port
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        cfg = AssociationConfig(
            calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=port
        )
        images = ops.find_images(
            cfg, ImageQuery(study_instance_uid="1.2.3", series_instance_uid="1.2.3.4")
        )
        assert len(images) == 1
        im = images[0]
        assert im.image_type == ["ORIGINAL", "PRIMARY"]
        assert im.content_date == "20200101"
        assert im.slice_thickness == 2.5
    finally:
        pacs.stop()


@pytest.mark.timeout(30)
def test_scu_find_round_trips_single_image_type(free_port) -> None:
    pacs = FakePacs(aet="FAKEPACS")
    ds = make_instance("1.2.3", "1.2.3.4", "1.2.3.4.1")
    ds.ImageType = "ORIGINAL"
    pacs.add_instance(ds)
    port = free_port()
    pacs.start(port)
    pacs.port = port
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        cfg = AssociationConfig(
            calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=port
        )
        images = ops.find_images(
            cfg, ImageQuery(study_instance_uid="1.2.3", series_instance_uid="1.2.3.4")
        )
        assert len(images) == 1
        assert images[0].image_type == ["ORIGINAL"]
    finally:
        pacs.stop()
