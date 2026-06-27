import pytest
from pydicom import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    StudyRootQueryRetrieveInformationModelFind,
    Verification,
)

from dimsechord.models import AssociationConfig, SeriesQuery, StudyQuery
from dimsechord.scu import DicomOperations
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
