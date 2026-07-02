import time

import pytest
from pydicom import Dataset
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    StudyRootQueryRetrieveInformationModelFind,
)

from dimsechord import AssociationPool, DicomNode, QueryEngine

FIND = StudyRootQueryRetrieveInformationModelFind


def _identifier() -> Dataset:
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = ""
    return ds


def _engine(pacs, aets=None, **pool_kwargs):
    pool = AssociationPool(aets=aets or ["POOLA", "POOLB"], **pool_kwargs)
    node = DicomNode(aet=pacs.aet, host="127.0.0.1", port=pacs.port)
    return QueryEngine(pool, node, find_timeout=10.0), pool


def _wait_zero_associations(pacs, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pacs.active_associations == 0:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.timeout(30)
def test_iter_find_streams_and_releases_lease(fake_pacs, seeded_study) -> None:
    engine, pool = _engine(fake_pacs, per_aet_find_cap=1)
    out = list(engine.iter_find(_identifier(), model=FIND))
    assert [str(d.StudyInstanceUID) for d in out] == seeded_study["study"]
    with pool.lease_find(timeout=0.5), pool.lease_find(timeout=0.5):
        pass  # every find slot is free again


@pytest.mark.timeout(30)
def test_iter_find_uses_pool_identity_as_calling_aet(fake_pacs) -> None:
    engine, _ = _engine(fake_pacs)
    list(engine.iter_find(_identifier(), model=FIND))
    assert fake_pacs.find_calling_aets[-1] in {"POOLA", "POOLB"}


@pytest.mark.timeout(30)
def test_early_close_releases_lease(fake_pacs) -> None:
    fake_pacs.find_response_delay = 0.3
    engine, pool = _engine(fake_pacs, aets=["POOLA"], per_aet_find_cap=1)
    gen = engine.iter_find(_identifier(), model=FIND)
    next(gen)
    gen.close()
    with pool.lease_find(timeout=1.0) as aet:  # slot free immediately
        assert aet == "POOLA"


@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_stream_find_async(fake_pacs, seeded_study) -> None:
    engine, _ = _engine(fake_pacs)
    out = [d async for d in engine.stream_find(_identifier(), model=FIND)]
    assert [str(d.StudyInstanceUID) for d in out] == seeded_study["study"]


@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_stream_find_early_close_releases_lease(fake_pacs) -> None:
    fake_pacs.find_response_delay = 0.3
    engine, pool = _engine(fake_pacs, aets=["POOLA"], per_aet_find_cap=1)
    agen = engine.stream_find(_identifier(), model=FIND)
    await anext(agen)
    await agen.aclose()
    with pool.lease_find(timeout=1.0) as aet:  # slot free immediately
        assert aet == "POOLA"
    assert _wait_zero_associations(fake_pacs)
