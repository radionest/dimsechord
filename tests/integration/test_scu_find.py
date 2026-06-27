import pytest
from pydicom import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    StudyRootQueryRetrieveInformationModelFind,
    Verification,
)

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
