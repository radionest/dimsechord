"""Raw-pynetdicom smoke test for the scripted Store-SCP fixture."""

import time

import pytest
from pydicom.uid import ImplicitVRLittleEndian
from pynetdicom import AE
from pynetdicom.sop_class import CTImageStorage  # type: ignore[attr-defined]

from dimsechord import build_storage_scu_contexts
from tests.factories import make_instance
from tests.fake_pacs import ScriptedStoreScp


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached within deadline")


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
