import threading

import pytest

from dimsechord.models import AssociationConfig, QueryRetrieveLevel, RetrieveRequest
from dimsechord.scp import StorageSCP
from dimsechord.scu import DicomOperations


@pytest.mark.timeout(60)
def test_move_study_to_external_scp(fake_pacs, seeded_study, free_port) -> None:
    dest_aet = "EXTDEST"
    dest_port = free_port()
    scp = StorageSCP()
    scp.start(aets=dest_aet, port=dest_port)
    fake_pacs.register_destination(dest_aet, "127.0.0.1", dest_port)

    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    key = f"{study}/{series}"
    session = scp.register_session(key)

    collected: list[str] = []

    def _drain() -> None:
        while True:
            item = session.queue.get(timeout=30)
            if item is None:
                break
            collected.append(item[0])

    drain = threading.Thread(target=_drain)
    drain.start()
    try:
        ops = DicomOperations(calling_aet="MOVESCU")
        config = AssociationConfig(
            calling_aet="MOVESCU", called_aet=fake_pacs.aet,
            peer_host="127.0.0.1", peer_port=fake_pacs.port,
        )
        request = RetrieveRequest(
            level=QueryRetrieveLevel.SERIES, study_instance_uid=study, series_instance_uid=series
        )
        result = ops.move_study(config, request, destination_aet=dest_aet)
        scp.signal_end(key)
        drain.join(timeout=10)

        assert result.num_completed == 2
        assert set(collected) == set(seeded_study[series])
    finally:
        scp.stop()
