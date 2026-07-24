"""Raw-pynetdicom smoke test for the scripted Store-SCP fixture, plus StoreSession."""

import time

import pytest
from pydicom.uid import ImplicitVRLittleEndian
from pynetdicom import AE
from pynetdicom.sop_class import CTImageStorage  # type: ignore[attr-defined]

from dimsechord import AssociationError, DicomNode, build_storage_scu_contexts
from dimsechord._store_session import StoreSession
from tests.factories import make_instance
from tests.fake_pacs import ScriptedStoreScp


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached within deadline")


@pytest.fixture
def scripted_scp(free_port):
    """A running ScriptedStoreScp plus a DicomNode peer pointing at it."""
    scp = ScriptedStoreScp()
    port = free_port()
    scp.start(port)
    peer = DicomNode(aet="STORESCP", host="127.0.0.1", port=port)
    try:
        yield scp, peer
    finally:
        scp.stop()


@pytest.mark.timeout(60)
def test_scripted_store_scp_accepts_ct_instance(free_port, seeded_study) -> None:
    """Raw AE + build_storage_scu_contexts() round-trips one CT instance."""
    scp = ScriptedStoreScp()
    port = free_port()
    scp.start(port)
    try:
        study = seeded_study["study"][0]
        series = seeded_study["series"][0]
        sop_uid = seeded_study[series][0]
        ds = make_instance(study, series, sop_uid, modality="CT")
        ds.SOPClassUID = CTImageStorage
        ds.file_meta.MediaStorageSOPClassUID = CTImageStorage

        ae = AE(ae_title="SENDER")
        ae.requested_contexts = build_storage_scu_contexts()
        assoc = ae.associate("127.0.0.1", port, ae_title="STORESCP")
        assert assoc.is_established
        try:
            status = assoc.send_c_store(ds)
            assert status.Status == 0x0000
        finally:
            assoc.release()

        _wait_until(lambda: scp.associations == 1)
        # Both sides offer the same default matrix (Implicit VR LE listed
        # first); the acceptor negotiates the first mutually-supported TS
        # regardless of the dataset's own encoding, and pynetdicom transcodes
        # the uncompressed dataset to match (verbatim forwarding applies to
        # status codes, not to uncompressed wire encoding).
        assert scp.received == [(sop_uid, str(ImplicitVRLittleEndian))]
        assert scp.associations == 1
    finally:
        scp.stop()


# ── StoreSession: lifecycle, verbatim status pass-through ──────────


def test_construction_with_nobody_listening_succeeds(free_port) -> None:
    """No I/O happens at construction — the association opens lazily on first store()."""
    peer = DicomNode(aet="STORESCP", host="127.0.0.1", port=free_port())
    StoreSession(peer, calling_aet="SENDER")  # must not raise or hang


@pytest.mark.timeout(30)
def test_first_store_opens_association_and_returns_success(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp
    session = StoreSession(peer, calling_aet="SENDER")
    try:
        study = seeded_study["study"][0]
        series = seeded_study["series"][0]
        sop_uid = seeded_study[series][0]
        ds = make_instance(study, series, sop_uid)
        assert session.store(ds) == 0x0000
        assert scp.associations == 1
    finally:
        session.close()


@pytest.mark.timeout(30)
def test_second_store_reuses_the_association(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp
    session = StoreSession(peer, calling_aet="SENDER")
    try:
        study = seeded_study["study"][0]
        series = seeded_study["series"][0]
        sop_uid_1, sop_uid_2 = seeded_study[series][0], seeded_study[series][1]
        assert session.store(make_instance(study, series, sop_uid_1)) == 0x0000
        assert session.store(make_instance(study, series, sop_uid_2)) == 0x0000
        assert scp.associations == 1
    finally:
        session.close()


@pytest.mark.timeout(30)
def test_store_returns_warning_status_verbatim(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp
    scp.statuses = [0xB000]
    session = StoreSession(peer, calling_aet="SENDER")
    try:
        study = seeded_study["study"][0]
        series = seeded_study["series"][0]
        sop_uid = seeded_study[series][0]
        ds = make_instance(study, series, sop_uid)
        assert session.store(ds) == 0xB000
    finally:
        session.close()


@pytest.mark.timeout(30)
def test_store_returns_failure_status_verbatim(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp
    scp.statuses = [0xC001]
    session = StoreSession(peer, calling_aet="SENDER")
    try:
        study = seeded_study["study"][0]
        series = seeded_study["series"][0]
        sop_uid = seeded_study[series][0]
        ds = make_instance(study, series, sop_uid)
        assert session.store(ds) == 0xC001
    finally:
        session.close()


@pytest.mark.timeout(30)
def test_close_twice_is_a_noop(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp

    never_opened = StoreSession(peer, calling_aet="SENDER")
    never_opened.close()
    never_opened.close()  # no association was ever opened; must not raise

    session = StoreSession(peer, calling_aet="SENDER")
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    sop_uid = seeded_study[series][0]
    session.store(make_instance(study, series, sop_uid))
    session.close()
    session.close()  # already closed; must not raise or double-release
    assert scp.associations == 1


@pytest.mark.timeout(30)
def test_store_after_close_reopens_association(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp
    session = StoreSession(peer, calling_aet="SENDER")
    try:
        study = seeded_study["study"][0]
        series = seeded_study["series"][0]
        sop_uid_1, sop_uid_2 = seeded_study[series][0], seeded_study[series][1]
        assert session.store(make_instance(study, series, sop_uid_1)) == 0x0000
        session.close()
        assert session.store(make_instance(study, series, sop_uid_2)) == 0x0000
        assert scp.associations == 2
    finally:
        session.close()


@pytest.mark.timeout(30)
def test_context_manager_closes_association_on_exit(scripted_scp, seeded_study) -> None:
    scp, peer = scripted_scp
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    sop_uid = seeded_study[series][0]
    with StoreSession(peer, calling_aet="SENDER") as session:
        assert session.store(make_instance(study, series, sop_uid)) == 0x0000
        _wait_until(lambda: len(scp._server.active_associations) == 1)
    # __exit__ released the association; the SCP-side live count drops back to 0.
    _wait_until(lambda: len(scp._server.active_associations) == 0)


@pytest.mark.timeout(30)
def test_store_against_dead_port_raises_association_error(free_port, seeded_study) -> None:
    peer = DicomNode(aet="STORESCP", host="127.0.0.1", port=free_port())
    session = StoreSession(peer, calling_aet="SENDER")
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    sop_uid = seeded_study[series][0]
    ds = make_instance(study, series, sop_uid)
    with pytest.raises(AssociationError):
        session.store(ds)
