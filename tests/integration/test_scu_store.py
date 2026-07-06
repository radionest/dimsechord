"""Compressed C-STORE send path: contexts derived from the datasets."""

import pytest
from pydicom.uid import JPEGLSLossless

from dimsechord._models import AssociationConfig
from dimsechord._scp import StorageSCP
from dimsechord._scu import DicomOperations
from tests.factories import make_compressed_instance, make_instance


@pytest.mark.timeout(60)
def test_store_batch_with_compressed_arrives_verbatim(free_port, seeded_study) -> None:
    receiver = StorageSCP()
    port = free_port()
    receiver.start({"DEST": port}, ip="127.0.0.1")
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    session = receiver.register_session(f"{study}/{series}")
    plain_uid, comp_uid = seeded_study[series][0], seeded_study[series][1]
    datasets = [
        make_instance(study, series, plain_uid),
        make_compressed_instance(study, series, comp_uid),
    ]
    ops = DicomOperations(calling_aet="SENDER")
    config = AssociationConfig(
        calling_aet="SENDER", called_aet="DEST", peer_host="127.0.0.1", peer_port=port
    )
    try:
        result = ops.store_instances_batch(config, datasets)
        assert result.total_sent == 2
        assert result.total_failed == 0
        received = {}
        for _ in range(2):
            sop_uid, ds = session.queue.get(timeout=10)
            received[sop_uid] = ds
        assert received[comp_uid].file_meta.TransferSyntaxUID == JPEGLSLossless
    finally:
        receiver.stop()
