import pytest
from pydicom.uid import generate_uid
from pynetdicom import AE
from pynetdicom.sop_class import Verification  # type: ignore[attr-defined]

from dimsechord._cache import DicomCache
from dimsechord._models import DicomNode
from dimsechord._pool import AssociationPool
from dimsechord._pull_engine import PullEngine
from dimsechord._scp import StorageSCP
from tests.factories import make_instance
from tests.fake_pacs import FakePacs


@pytest.mark.timeout(30)
def test_strict_pacs_rejects_unregistered_calling_aet(free_port) -> None:
    """The strict FakePacs refuses an association whose calling AET is unknown."""
    pacs = FakePacs(aet="STRICTPACS")
    pacs_port = free_port()
    pacs.start(pacs_port, require_calling_aets=["KNOWN"])
    try:
        ae = AE(ae_title="STRANGER")
        ae.add_requested_context(Verification)
        assoc = ae.associate("127.0.0.1", pacs_port, ae_title="STRICTPACS")
        assert not assoc.is_established
        if assoc.is_established:  # pragma: no cover - defensive cleanup
            assoc.release()
    finally:
        pacs.stop()


@pytest.mark.timeout(120)
def test_pool_three_aets_each_on_own_port(free_port, tmp_path) -> None:
    """3 pooled AETs, each on its own port; strict PACS routes each AET -> its port."""
    aets = ["DESTA", "DESTB", "DESTC"]
    ports = {a: free_port() for a in aets}

    study = generate_uid()
    series = [generate_uid() for _ in range(3)]
    sops = {s: [generate_uid(), generate_uid()] for s in series}

    pacs = FakePacs(aet="STRICTPACS")
    for s in series:
        for sop in sops[s]:
            pacs.add_instance(make_instance(study, s, sop))
    pacs_port = free_port()
    pacs.start(pacs_port, require_calling_aets=aets)
    for a in aets:
        pacs.register_destination(a, "127.0.0.1", ports[a])

    pool = AssociationPool(aets=aets, per_aet_cap=1)
    scp = StorageSCP()
    scp.start({a: ports[a] for a in aets}, ip="127.0.0.1")

    cache = DicomCache(base_dir=tmp_path / "cache", index_path=tmp_path / "index.db")
    pacs_node = DicomNode(aet=pacs.aet, host="127.0.0.1", port=pacs_port)
    eng = PullEngine(
        pool=pool, scp=scp, cache=cache, pacs=pacs_node,
        cmove_timeout=60.0, arrival_timeout=30.0,
    )
    try:
        # Sequential retrievals round-robin the pool (A, B, C); each leased AET is
        # the C-MOVE destination, which the strict PACS routes to that AET's port.
        for s in series:
            received = list(eng.iter_series(study, s))
            assert {str(ds.SOPInstanceUID) for ds in received} == set(sops[s])
    finally:
        scp.stop()
        cache.shutdown()
        pacs.stop()
