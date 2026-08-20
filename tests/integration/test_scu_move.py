import threading
import time

import pytest
from pynetdicom import AE
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)

from dimsechord._exceptions import AssociationError
from dimsechord._models import (
    AssociationConfig,
    QueryRetrieveLevel,
    RetrieveRequest,
    StorageConfig,
    StorageMode,
)
from dimsechord._scp import StorageSCP
from dimsechord._scu import DicomOperations, MoveAbortHandle
from tests.fake_pacs import FakePacs


@pytest.mark.timeout(60)
def test_move_study_to_external_scp(fake_pacs, seeded_study, free_port) -> None:
    dest_aet = "EXTDEST"
    dest_port = free_port()
    scp = StorageSCP()
    scp.start({dest_aet: dest_port})
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
        result = ops.move(config, request, destination_aet=dest_aet)
        scp.signal_end(key)
        drain.join(timeout=10)

        assert result.num_completed == 2
        assert set(collected) == set(seeded_study[series])
    finally:
        scp.stop()


@pytest.mark.timeout(60)
def test_retrieve_via_move(fake_pacs, seeded_study, free_port) -> None:
    """retrieve_via_move completes end-to-end against the fake PACS."""
    dest_aet = "RVMDEST"
    dest_port = free_port()
    scp = StorageSCP()
    scp.start({dest_aet: dest_port})
    fake_pacs.register_destination(dest_aet, "127.0.0.1", dest_port)

    study = seeded_study["study"][0]
    series = seeded_study["series"][0]

    ops = DicomOperations(calling_aet=dest_aet)
    config = AssociationConfig(
        calling_aet=dest_aet,
        called_aet=fake_pacs.aet,
        peer_host="127.0.0.1",
        peer_port=fake_pacs.port,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=study,
        series_instance_uid=series,
    )
    storage = StorageConfig(mode=StorageMode.MEMORY)
    try:
        result = ops.retrieve_via_move(
            config, request, storage, local_aet=dest_aet, scp=scp, timeout=30.0
        )
        assert result.num_completed == 2
        assert result.instances
    finally:
        scp.stop()


@pytest.mark.timeout(60)
def test_retrieve_via_move_session_is_collect_shaped(fake_pacs, seeded_study, free_port) -> None:
    """retrieve_via_move retains instances without double-buffering the queue."""
    dest_aet = "COLLECTDEST"
    dest_port = free_port()
    scp = StorageSCP()
    scp.start({dest_aet: dest_port})
    fake_pacs.register_destination(dest_aet, "127.0.0.1", dest_port)
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    captured: list = []
    original = scp.register_session
    def spying_register(key, **kwargs):
        session = original(key, **kwargs)
        captured.append(session)
        return session
    scp.register_session = spying_register  # type: ignore[method-assign]
    try:
        ops = DicomOperations(calling_aet="MOVESCU")
        config = AssociationConfig(
            calling_aet="MOVESCU", called_aet=fake_pacs.aet,
            peer_host="127.0.0.1", peer_port=fake_pacs.port,
        )
        request = RetrieveRequest(
            level=QueryRetrieveLevel.SERIES,
            study_instance_uid=study, series_instance_uid=series,
        )
        result = ops.retrieve_via_move(
            config, request, StorageConfig(mode=StorageMode.MEMORY),
            local_aet=dest_aet, scp=scp,
        )
        assert set(result.instances) == set(seeded_study[series])
        assert captured[0].collect is True
        assert captured[0].queue.qsize() == 0  # nothing double-buffered
    finally:
        scp.stop()


def test_retrieve_via_move_requires_running_scp(fake_pacs, seeded_study) -> None:
    """retrieve_via_move raises RuntimeError when the SCP is not running."""
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]

    ops = DicomOperations(calling_aet="NOTSCP")
    config = AssociationConfig(
        calling_aet="NOTSCP",
        called_aet=fake_pacs.aet,
        peer_host="127.0.0.1",
        peer_port=fake_pacs.port,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=study,
        series_instance_uid=series,
    )
    storage = StorageConfig(mode=StorageMode.MEMORY)
    stopped_scp = StorageSCP()  # never started

    with pytest.raises(RuntimeError, match="Storage SCP not running"):
        ops.retrieve_via_move(config, request, storage, local_aet="NOTSCP", scp=stopped_scp)


@pytest.mark.timeout(30)
def test_move_identifier_is_study_root_unique_keys_only(fake_pacs, seeded_study) -> None:
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    ops = DicomOperations(calling_aet="MOVESCU")
    config = AssociationConfig(
        calling_aet="MOVESCU", called_aet=fake_pacs.aet,
        peer_host="127.0.0.1", peer_port=fake_pacs.port,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES, study_instance_uid=study, series_instance_uid=series
    )
    # unknown dest is fine: capture happens first
    ops.move(config, request, destination_aet="NOWHERE")
    assert fake_pacs.move_contexts[-1] == StudyRootQueryRetrieveInformationModelMove
    assert sorted(el.keyword for el in fake_pacs.move_identifiers[-1]) == [
        "QueryRetrieveLevel", "SeriesInstanceUID", "StudyInstanceUID",
    ]


@pytest.mark.timeout(30)
def test_move_context_refused_raises_association_error(free_port) -> None:
    pacs = FakePacs(aet="FINDONLY")
    port = free_port()
    pacs.start(port, qr_contexts=[StudyRootQueryRetrieveInformationModelFind])
    try:
        ops = DicomOperations(calling_aet="MOVESCU")
        config = AssociationConfig(
            calling_aet="MOVESCU", called_aet="FINDONLY", peer_host="127.0.0.1", peer_port=port
        )
        request = RetrieveRequest(level=QueryRetrieveLevel.STUDY, study_instance_uid="1.2.3")
        with pytest.raises(AssociationError, match="Study Root C-MOVE"):
            ops.move(config, request, destination_aet="ANY")
    finally:
        pacs.stop()


@pytest.mark.timeout(30)
def test_retrieve_via_move_context_refused_raises_association_error(free_port) -> None:
    """Peer accepts SR-FIND but not SR-MOVE → retrieve_via_move wraps the refusal.

    The refusal fires at send_c_move(), before any C-STORE sub-operation flows, so
    the StorageSCP never actually receives anything — only needs to be running.
    """
    pacs = FakePacs(aet="FINDONLY2")
    port = free_port()
    pacs.start(port, qr_contexts=[StudyRootQueryRetrieveInformationModelFind])
    dest_aet = "RVMREFUSED"
    scp = StorageSCP()
    scp.start({dest_aet: free_port()})
    try:
        ops = DicomOperations(calling_aet=dest_aet)
        config = AssociationConfig(
            calling_aet=dest_aet, called_aet="FINDONLY2", peer_host="127.0.0.1", peer_port=port
        )
        request = RetrieveRequest(level=QueryRetrieveLevel.STUDY, study_instance_uid="1.2.3")
        storage = StorageConfig(mode=StorageMode.MEMORY)
        with pytest.raises(AssociationError, match="Study Root C-MOVE"):
            ops.retrieve_via_move(config, request, storage, local_aet=dest_aet, scp=scp)
    finally:
        scp.stop()
        pacs.stop()


@pytest.mark.timeout(30)
def test_move_pre_aborted_handle_never_dispatches(fake_pacs, seeded_study) -> None:
    handle = MoveAbortHandle()
    handle.abort()  # consumer gone before the association even exists
    ops = DicomOperations(calling_aet="ABORTER")
    config = AssociationConfig(
        calling_aet="ABORTER", called_aet=fake_pacs.aet,
        peer_host="127.0.0.1", peer_port=fake_pacs.port,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=seeded_study["study"][0],
        series_instance_uid=seeded_study["series"][0],
    )
    with pytest.raises(AssociationError, match="cancelled before dispatch"):
        ops.move(config, request, destination_aet="NOWHERE", abort_handle=handle)
    assert fake_pacs.move_identifiers == []  # the C-MOVE request never went out


@pytest.mark.timeout(30)
def test_move_cross_thread_abort_ends_early_and_never_success(
    fake_pacs, seeded_study, free_port, monkeypatch
) -> None:
    """Cross-thread abort during a stalled DIMSE wait.

    Observed pynetdicom 3.0.4 behavior: a thread already parked inside
    ``send_c_move()``'s response loop, blocked waiting for the next DIMSE
    message, is NOT woken by another thread's ``assoc.abort()`` — verified
    both by reading pynetdicom's source (``dimse.get_msg`` blocks on
    ``Queue.get(timeout=dimse_timeout)``; nothing pushes a wakeup on abort)
    and by an isolated timing probe: with the peer silent, ``move()`` did not
    return until ~30.0 s — exactly ``dimse_timeout``'s default — regardless
    of an abort issued at 0.3 s. What IS prompt and reliable, confirmed by
    reading pynetdicom's C-MOVE SCP handling (``service_class.py``): the peer
    checks the requesting association before every further C-STORE
    sub-operation and stops immediately once it is gone, so no instance is
    ever delivered.

    To make the direct "never success" assertion live (not dead code behind
    a driver that's still blocked) without adding a production knob, this
    test monkeypatches ``DicomOperations._create_ae`` to shrink the move
    AE's ``dimse_timeout`` — abort can't wake the parked receive, so bounding
    it is what makes the parked call resolve inside the test instead of
    after the default 30 s. The test still drives move() from a background
    thread — mirroring the real driver-thread/consumer-thread split this
    handle is built for — and additionally verifies via the destination that
    nothing was ever delivered.
    """
    original_create_ae = DicomOperations._create_ae

    def create_ae_with_short_dimse(self: DicomOperations) -> AE:
        ae = original_create_ae(self)
        ae.dimse_timeout = 2.0  # abort can't wake a parked DIMSE receive; bound it for the test
        return ae

    monkeypatch.setattr(DicomOperations, "_create_ae", create_ae_with_short_dimse)

    dest_aet, dest_port = "ABORTDEST", free_port()
    scp = StorageSCP()
    scp.start({dest_aet: dest_port})
    fake_pacs.register_destination(dest_aet, "127.0.0.1", dest_port)
    fake_pacs.move_response_delay = 1.0  # 2 instances → ~2 s full move
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    session = scp.register_session(f"{study}/{series}", collect=True)

    handle = MoveAbortHandle()
    threading.Timer(0.3, handle.abort).start()
    ops = DicomOperations(calling_aet="MOVESCU")
    config = AssociationConfig(
        calling_aet="MOVESCU", called_aet=fake_pacs.aet,
        peer_host="127.0.0.1", peer_port=fake_pacs.port,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=study, series_instance_uid=series,
    )
    outcome: dict[str, str] = {}

    def _drive() -> None:
        try:
            outcome["status"] = ops.move(
                config, request, destination_aet=dest_aet, abort_handle=handle
            ).status
        except AssociationError:
            outcome["status"] = "raised"  # also acceptable: abort surfaced as association failure

    driver = threading.Thread(target=_drive, daemon=True)
    start = time.monotonic()
    driver.start()
    driver.join(timeout=6.0)  # shrunk dimse_timeout resolves at ~2 s; generous slack
    elapsed = time.monotonic() - start
    scp.stop()

    assert not driver.is_alive()  # the driver exited — abort bounded it, not just the join
    assert elapsed < 4.0  # the consumer is never stuck waiting on the driver
    assert session.received_count == 0  # not one instance was ever delivered
    assert outcome["status"] != "success"  # an aborted move is never reported complete
