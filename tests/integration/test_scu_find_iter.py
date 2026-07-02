import time

import pytest
from pydicom import Dataset
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    StudyRootQueryRetrieveInformationModelFind,
)

from dimsechord._exceptions import FindFailedError
from dimsechord._models import AssociationConfig
from dimsechord._scu import DicomOperations
from tests.factories import make_instance
from tests.fake_pacs import FakePacs

FIND = StudyRootQueryRetrieveInformationModelFind


def _config(pacs) -> AssociationConfig:
    return AssociationConfig(
        calling_aet="TESTSCU", called_aet=pacs.aet, peer_host="127.0.0.1", peer_port=pacs.port
    )


def _identifier(**keys) -> Dataset:
    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = ""
    for k, v in keys.items():
        setattr(ds, k, v)
    return ds


def _wait_zero_associations(pacs, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pacs.active_associations == 0:
            return True
        time.sleep(0.05)
    return False


def _multi_study_pacs(free_port, n: int, delay: float) -> FakePacs:
    pacs = FakePacs()
    for i in range(1, n + 1):
        pacs.add_instance(make_instance(f"1.2.{i}", f"1.2.{i}.1", f"1.2.{i}.1.1"))
    pacs.find_response_delay = delay
    port = free_port()
    pacs.start(port)
    pacs.port = port
    return pacs


@pytest.mark.timeout(30)
def test_find_iter_yields_raw_datasets(fake_pacs, seeded_study) -> None:
    ops = DicomOperations(calling_aet="TESTSCU")
    out = list(ops.find_iter(_config(fake_pacs), _identifier(PatientName=""), FIND))
    assert [str(d.StudyInstanceUID) for d in out] == seeded_study["study"]
    assert str(out[0].PatientName)  # raw dataset carries what the PACS sent


@pytest.mark.timeout(30)
def test_find_iter_forwards_arbitrary_query_keys(fake_pacs) -> None:
    ops = DicomOperations(calling_aet="TESTSCU")
    ident = _identifier(PatientName="ИВАНОВ*", StudyDate="20260101-20260201")
    list(ops.find_iter(_config(fake_pacs), ident, FIND))
    seen = fake_pacs.find_identifiers[-1]
    assert str(seen.PatientName) == "ИВАНОВ*"
    assert str(seen.StudyDate) == "20260101-20260201"


@pytest.mark.timeout(30)
def test_find_iter_streams_before_completion(free_port) -> None:
    pacs = _multi_study_pacs(free_port, n=3, delay=0.4)
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        stamps: list[float] = []
        for _ in ops.find_iter(_config(pacs), _identifier(), FIND):
            stamps.append(time.monotonic())
        assert len(stamps) == 3
        assert stamps[-1] - stamps[0] >= 0.6  # first arrived well before the last
    finally:
        pacs.stop()


@pytest.mark.timeout(30)
def test_find_iter_raises_on_failure_status(fake_pacs) -> None:
    fake_pacs.fail_find_with = 0xA700
    ops = DicomOperations(calling_aet="TESTSCU")
    with pytest.raises(FindFailedError) as ei:
        list(ops.find_iter(_config(fake_pacs), _identifier(), FIND))
    assert ei.value.status == 0xA700


@pytest.mark.timeout(30)
def test_find_iter_close_aborts_association(free_port) -> None:
    pacs = _multi_study_pacs(free_port, n=5, delay=0.3)
    try:
        ops = DicomOperations(calling_aet="TESTSCU")
        gen = ops.find_iter(_config(pacs), _identifier(), FIND)
        next(gen)
        gen.close()  # abort, not drain
        # 4 remaining responses x 0.3s delay: a graceful drain needs >=1.2s, so a
        # 1.0s bound discriminates abort (~50ms) from drain with ~20x margin.
        assert _wait_zero_associations(pacs, timeout=1.0)
    finally:
        pacs.stop()
