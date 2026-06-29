import pytest
from pynetdicom import AE
from pynetdicom.sop_class import MRImageStorage  # type: ignore[attr-defined]

from dimsechord._scp import StorageSCP
from tests.factories import make_instance


def test_scp_binds_distinct_port_per_aet(free_port, seeded_study) -> None:
    """Each AET listens on its own port; a C-STORE to one port still routes by UID."""
    scp = StorageSCP()
    port_a, port_b = free_port(), free_port()
    scp.start({"DESTA": port_a, "DESTB": port_b}, ip="127.0.0.1")
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    session = scp.register_session(f"{study}/{series}")
    inst = make_instance(study, series, seeded_study[series][0])
    try:
        ae = AE(ae_title="SENDER")
        ae.add_requested_context(MRImageStorage)
        # Address the AET bound on the SECOND port; connect to that port.
        assoc = ae.associate("127.0.0.1", port_b, ae_title="DESTB")
        assert assoc.is_established
        try:
            assert assoc.send_c_store(inst).Status == 0x0000
        finally:
            assoc.release()
        sop_uid, _ds = session.queue.get(timeout=10)
        assert sop_uid == seeded_study[series][0]
        assert session.received_count == 1
    finally:
        scp.stop()


def test_start_empty_bindings_raises() -> None:
    scp = StorageSCP()
    with pytest.raises(ValueError):
        scp.start({})


def test_double_start_is_ignored(free_port, caplog) -> None:
    import logging

    scp = StorageSCP()
    scp.start({"DEST": free_port()})
    try:
        with caplog.at_level(logging.WARNING, logger="dimsechord._scp"):
            scp.start({"DEST2": free_port()})  # already running → warn + return
        assert scp.is_running
        assert any("already running" in r.message for r in caplog.records)
    finally:
        scp.stop()


@pytest.mark.timeout(30)
def test_partial_bind_rollback_on_oserror(free_port) -> None:
    """If a later port bind fails, already-started servers are rolled back and
    a clean retry must succeed."""
    # Occupy port_b with a running SCP so the second bind raises OSError.
    port_b = free_port()
    occupier = StorageSCP()
    occupier.start({"OCCUPIER": port_b}, ip="127.0.0.1")

    scp = StorageSCP()
    port_a = free_port()
    try:
        with pytest.raises(OSError):
            # port_a binds fine; port_b is already in use → should roll back port_a
            scp.start({"A": port_a, "B": port_b}, ip="127.0.0.1")
        assert scp.is_running is False
        # Clean retry on a fresh port must succeed
        scp.start({"A": free_port()}, ip="127.0.0.1")
        assert scp.is_running is True
    finally:
        scp.stop()
        occupier.stop()


@pytest.fixture
def running_scp(free_port):
    scp = StorageSCP()
    port = free_port()
    scp.start({"DEST1": port, "DEST2": port})
    try:
        yield scp, port
    finally:
        scp.stop()


@pytest.mark.timeout(30)
def test_scp_streams_instances_onto_queue(running_scp, seeded_study) -> None:
    scp, port = running_scp
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    key = f"{study}/{series}"
    session = scp.register_session(key)

    inst = make_instance(study, series, seeded_study[series][0])

    ae = AE(ae_title="SENDER")
    ae.add_requested_context(MRImageStorage)
    # called-AET is a pool AET the SCP did not bind as its primary title
    assoc = ae.associate("127.0.0.1", port, ae_title="DEST2")
    assert assoc.is_established
    try:
        status = assoc.send_c_store(inst)
        assert status.Status == 0x0000
    finally:
        assoc.release()

    sop_uid, ds = session.queue.get(timeout=10)
    assert sop_uid == seeded_study[series][0]
    assert str(ds.StudyInstanceUID) == study
    assert session.received_count == 1


@pytest.mark.timeout(30)
def test_signal_end_pushes_sentinel(running_scp, seeded_study) -> None:
    scp, _ = running_scp
    key = f"{seeded_study['study'][0]}/{seeded_study['series'][0]}"
    session = scp.register_session(key)
    scp.signal_end(key)
    assert session.ended is True
    assert session.queue.get(timeout=2) is None


def test_register_duplicate_key_raises(running_scp) -> None:
    scp, _ = running_scp
    scp.register_session("dup")
    with pytest.raises(RuntimeError):
        scp.register_session("dup")


@pytest.mark.timeout(30)
def test_shared_port_two_aets_route_by_uid(free_port, seeded_study) -> None:
    """Two AETs sharing one port; C-STOREs route to their respective sessions by UID."""
    port = free_port()
    scp = StorageSCP()
    scp.start({"SHARE_A": port, "SHARE_B": port}, ip="127.0.0.1")

    study = seeded_study["study"][0]
    series_a = seeded_study["series"][0]
    series_b = seeded_study["series"][1]
    session_a = scp.register_session(f"{study}/{series_a}")
    session_b = scp.register_session(f"{study}/{series_b}")

    inst_a = make_instance(study, series_a, seeded_study[series_a][0])
    inst_b = make_instance(study, series_b, seeded_study[series_b][0])

    ae = AE(ae_title="SENDER")
    ae.add_requested_context(MRImageStorage)
    try:
        assoc_a = ae.associate("127.0.0.1", port, ae_title="SHARE_A")
        assert assoc_a.is_established
        try:
            assert assoc_a.send_c_store(inst_a).Status == 0x0000
        finally:
            assoc_a.release()

        assoc_b = ae.associate("127.0.0.1", port, ae_title="SHARE_B")
        assert assoc_b.is_established
        try:
            assert assoc_b.send_c_store(inst_b).Status == 0x0000
        finally:
            assoc_b.release()

        sop_a, _ = session_a.queue.get(timeout=10)
        assert sop_a == seeded_study[series_a][0]
        assert session_a.received_count == 1

        sop_b, _ = session_b.queue.get(timeout=10)
        assert sop_b == seeded_study[series_b][0]
        assert session_b.received_count == 1
    finally:
        scp.stop()
