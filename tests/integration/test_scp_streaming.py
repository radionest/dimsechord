import threading
import time
from types import SimpleNamespace

import pytest
from pydicom.uid import ExplicitVRLittleEndian, JPEGLSLossless
from pynetdicom import AE
from pynetdicom.sop_class import MRImageStorage  # type: ignore[attr-defined]

from dimsechord._scp import StorageSCP
from tests.factories import make_compressed_instance, make_instance


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


@pytest.mark.timeout(10)
def test_completion_by_session_end_does_not_count_as_arrival() -> None:
    """A ``done`` event fired by shutdown/session-end (``stop``/``signal_end``) with an
    unmet expectation must read as incomplete, so a truncated series is never certified
    complete (issue #15). ``signal_end`` sets the same ``ended`` + ``done`` state per
    session that ``stop`` broadcasts, so it exercises the same race deterministically."""
    scp = StorageSCP()

    # Only k<N arrived, so ``done`` is NOT set by arrival; session-end sets it instead.
    scp.register_session("study/short")
    scp.set_expected("study/short", 3)  # expect 3; none arrived
    scp.signal_end("study/short")  # end-of-stream: sets ended + done, keeps the session
    assert scp.wait_for_completion("study/short", timeout=0.5) is False

    # Positive control: a genuine arrival (``done`` from a count match, ended False)
    # must still read as complete — the guard must not break normal completion.
    session = scp.register_session("study/full")
    session.received_count = 2
    scp.set_expected("study/full", 2)  # received >= expected → done set, ended stays False
    assert scp.wait_for_completion("study/full", timeout=0.5) is True


@pytest.mark.timeout(10)
def test_store_item_enqueued_before_done_fires(seeded_study) -> None:
    """The C-STORE item must reach the queue before ``done`` fires (issue #15).

    On the final instance of a successful C-MOVE, ``done`` firing lets the driver's
    ``wait_for_completion`` return and run ``signal_end``, enqueuing the ``None``
    sentinel. If ``done`` is set before the item is queued, that sentinel can overtake
    the item: the consumer breaks on the sentinel having yielded N-1 of N and
    ``_finalize_fetch`` durably certifies the short series. Assert the item is already
    queued at the instant ``done`` fires.
    """
    scp = StorageSCP()
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    key = f"{study}/{series}"
    session = scp.register_session(key)
    scp.set_expected(key, 1)  # a single instance completes the session → fires done

    qsize_at_done: list[int] = []
    real_set = session.done.set

    def recording_set() -> None:
        qsize_at_done.append(session.queue.qsize())
        real_set()

    session.done.set = recording_set  # type: ignore[method-assign]

    ds = make_instance(study, series, seeded_study[series][0])
    event = SimpleNamespace(dataset=ds, file_meta=ds.file_meta)
    assert scp._handle_store(event) == 0x0000

    assert session.done.is_set()  # done did fire (expectation met)
    assert qsize_at_done == [1]  # ...but only after the item was already on the queue


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


def test_scp_accepts_compressed_c_store_verbatim(free_port, seeded_study) -> None:
    """A JPEG-LS C-STORE is accepted and streamed out with its original TS intact."""
    scp = StorageSCP()
    port = free_port()
    scp.start({"DEST": port}, ip="127.0.0.1")
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    session = scp.register_session(f"{study}/{series}")
    inst = make_compressed_instance(study, series, seeded_study[series][0])
    try:
        ae = AE(ae_title="SENDER")
        ae.add_requested_context(MRImageStorage, [JPEGLSLossless])
        assoc = ae.associate("127.0.0.1", port, ae_title="DEST")
        assert assoc.is_established
        try:
            assert assoc.accepted_contexts, "SCP rejected the JPEG-LS context"
            assert assoc.send_c_store(inst).Status == 0x0000
        finally:
            assoc.release()
        sop_uid, ds = session.queue.get(timeout=10)
        assert sop_uid == seeded_study[series][0]
        assert ds.file_meta.TransferSyntaxUID == JPEGLSLossless
    finally:
        scp.stop()


def test_scp_custom_transfer_syntaxes_restrict_matching(free_port) -> None:
    """A restricted TS list still works: JPEG-LS-only proposals get no accepted context.

    pynetdicom aborts the whole association when zero presentation contexts
    negotiate successfully (see ``acse.py``'s "No accepted presentation contexts"
    branch), so ``is_established`` is False here rather than True. This differs
    from the SDD task brief's literal test, which asserted an established
    association with an empty ``accepted_contexts`` — verified against the
    installed pynetdicom to not hold; ``release()`` is a safe no-op either way.
    """
    scp = StorageSCP(supported_transfer_syntaxes=[ExplicitVRLittleEndian])
    port = free_port()
    scp.start({"DEST": port}, ip="127.0.0.1")
    try:
        ae = AE(ae_title="SENDER")
        ae.add_requested_context(MRImageStorage, [JPEGLSLossless])
        assoc = ae.associate("127.0.0.1", port, ae_title="DEST")
        try:
            assert not assoc.is_established
            assert not assoc.accepted_contexts
        finally:
            assoc.release()
    finally:
        scp.stop()


def test_maximum_associations_default_applied(free_port) -> None:
    scp = StorageSCP()
    scp.start({"CAPDEF": free_port()}, ip="127.0.0.1")
    try:
        assert all(ae.maximum_associations == 25 for ae in scp._aes)
    finally:
        scp.stop()


def test_maximum_associations_custom_applied(free_port) -> None:
    scp = StorageSCP(maximum_associations=7)
    scp.start({"CAPCUST": free_port()}, ip="127.0.0.1")
    try:
        assert all(ae.maximum_associations == 7 for ae in scp._aes)
    finally:
        scp.stop()


def test_register_session_shapes() -> None:
    scp = StorageSCP(session_queue_maxsize=3)
    streaming = scp.register_session("s/stream")
    collect = scp.register_session("s/collect", collect=True)
    assert streaming.collect is False and streaming.queue.maxsize == 3
    assert collect.collect is True


def test_streaming_session_does_not_retain_instances(running_scp, seeded_study) -> None:
    scp, port = running_scp
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    session = scp.register_session(f"{study}/{series}")
    inst = make_instance(study, series, seeded_study[series][0])
    ae = AE(ae_title="SENDER")
    ae.add_requested_context(MRImageStorage)
    assoc = ae.associate("127.0.0.1", port, ae_title="DEST1")
    assert assoc.is_established
    try:
        assert assoc.send_c_store(inst).Status == 0x0000
    finally:
        assoc.release()
    assert session.queue.get(timeout=10)[0] == seeded_study[series][0]
    assert session.instances == {}  # single buffering: streaming keeps nothing


def test_full_queue_drops_only_dead_sessions(seeded_study) -> None:
    """A full queue parks the handler while the session lives, drops once it ended."""
    scp = StorageSCP(session_queue_maxsize=1)
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    key = f"{study}/{series}"
    session = scp.register_session(key)
    ds = make_instance(study, series, seeded_study[series][0])
    event = SimpleNamespace(dataset=ds, file_meta=ds.file_meta)
    assert scp._handle_store(event) == 0x0000          # fills the queue
    assert session.received_count == 1

    # Second store on a full queue: flip ended from a timer → handler drops, returns.
    ds2 = make_instance(study, series, seeded_study[series][1])
    event2 = SimpleNamespace(dataset=ds2, file_meta=ds2.file_meta)
    timer = threading.Timer(0.6, lambda: setattr(session, "ended", True))
    timer.start()
    start = time.monotonic()
    assert scp._handle_store(event2) == 0x0000
    assert 0.4 < time.monotonic() - start < 5.0        # parked, then gave up on ended
    assert session.received_count == 1                 # dropped item was never counted
    timer.join()


def test_full_queue_backpressure_releases_on_drain(seeded_study) -> None:
    scp = StorageSCP(session_queue_maxsize=1)
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    session = scp.register_session(f"{study}/{series}")
    for sop in seeded_study[series]:  # 2 instances; queue holds 1
        ds = make_instance(study, series, sop)
        event = SimpleNamespace(dataset=ds, file_meta=ds.file_meta)
        threading.Timer(0.5, session.queue.get).start()  # a consumer drains later
        assert scp._handle_store(event) == 0x0000
    assert session.received_count == 2                 # both eventually counted


def test_signal_end_sentinel_waits_for_live_consumer(seeded_study) -> None:
    """ended=True is set before the sentinel put; the sentinel must still be
    delivered to a live consumer even if the queue is momentarily full."""
    scp = StorageSCP(session_queue_maxsize=1)
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    key = f"{study}/{series}"
    session = scp.register_session(key)
    session.queue.put(("sop", None))                   # fill the queue
    got: list = []
    def drain() -> None:
        time.sleep(0.5)
        got.append(session.queue.get(timeout=5))       # frees the slot
        got.append(session.queue.get(timeout=5))       # then receives the sentinel
    t = threading.Thread(target=drain)
    t.start()
    scp.signal_end(key)                                # must not wedge; sentinel lands
    t.join(timeout=10)
    assert got[1] is None
