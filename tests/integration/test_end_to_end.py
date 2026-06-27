import pytest

from dimsechord import (
    AssociationPool,
    CacheIndex,
    DicomCache,
    DicomClient,
    DicomNode,
    DicomOperations,
    PullEngine,
    StorageSCP,
    StudyQuery,
    convert_datasets_to_dicom_json,
)


@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_qido_then_wado_then_cross_face_hit(fake_pacs, seeded_study, free_port, tmp_path):
    study = seeded_study["study"][0]
    series = seeded_study["series"][0]
    peer = DicomNode(aet=fake_pacs.aet, host="127.0.0.1", port=fake_pacs.port)

    # Flow 1: QIDO study list via C-FIND.
    client = DicomClient(calling_aet="E2ESCU")
    studies = await client.find_studies(StudyQuery(), peer)
    assert [s.study_instance_uid for s in studies] == [study]

    # Wire the pull-engine (the WADO retrieve path).
    scp_port = free_port()
    pool = AssociationPool(aets=["E2EDEST"])
    scp = StorageSCP()
    scp.start(aets=pool.aets, port=scp_port)
    fake_pacs.register_destination("E2EDEST", "127.0.0.1", scp_port)
    idx = CacheIndex(str(tmp_path / "i.db"))
    cache = DicomCache(base_dir=tmp_path / "c", index=idx)
    ops = DicomOperations(calling_aet="E2EDEST")
    engine = PullEngine(pool=pool, scp=scp, cache=cache, index=idx, ops=ops, pacs=peer,
                        cmove_timeout=60.0, arrival_timeout=30.0)
    try:
        # Flow 2: WADO retrieve via move-to-self, build DICOM-JSON metadata.
        cached = await engine.ensure_series(study, series)
        metadata = convert_datasets_to_dicom_json(
            list(cached.instances.values()), base_url="http://x/dicom-web"
        )
        assert len(metadata) == len(seeded_study[series])
        assert all("0020000D" in m for m in metadata)  # StudyInstanceUID present

        cache.flush_pending_writes()  # ensure tees landed on disk + index

        # Cross-face hit: drop the memory tier so the next read must come from the
        # shared disk/index — proving "pulled for one face → available to the other"
        # (§6). A fresh engine whose SCP has no registered destination can't pull upstream.
        cache._memory_cache.clear()
        scp.stop()
        engine2 = PullEngine(pool=pool, scp=StorageSCP(), cache=cache, index=idx, ops=ops,
                             pacs=peer, cmove_timeout=5.0, arrival_timeout=5.0)
        again = [ds async for ds in engine2.stream_series(study, series)]
        assert {str(d.SOPInstanceUID) for d in again} == set(seeded_study[series])
    finally:
        cache.shutdown()
        idx.close()
