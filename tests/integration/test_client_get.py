import pytest

from dimsechord._client import DicomClient
from dimsechord._models import DicomNode


@pytest.fixture
def peer(fake_pacs) -> DicomNode:
    return DicomNode(aet=fake_pacs.aet, host="127.0.0.1", port=fake_pacs.port)


@pytest.mark.timeout(60)
async def test_get_series_to_memory(seeded_study, peer) -> None:
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    client = DicomClient(calling_aet="GETSCU")
    result = await client.get_series_to_memory(study, series, peer)
    assert set(result.instances) == set(seeded_study[series])


@pytest.mark.timeout(60)
async def test_get_series_to_disk(seeded_study, peer, tmp_path) -> None:
    study, series = seeded_study["study"][0], seeded_study["series"][0]
    client = DicomClient(calling_aet="GETSCU")
    out = tmp_path / "out"
    result = await client.get_series(study, series, peer, output_dir=out)
    assert result.num_completed == 2
    assert {p.stem for p in out.glob("*.dcm")} == set(seeded_study[series])


@pytest.mark.timeout(60)
async def test_get_study_to_memory(seeded_study, peer) -> None:
    study = seeded_study["study"][0]
    s1, s2 = seeded_study["series"]
    client = DicomClient(calling_aet="GETSCU")
    result = await client.get_study_to_memory(study, peer)
    assert set(result.instances) == set(seeded_study[s1]) | set(seeded_study[s2])
