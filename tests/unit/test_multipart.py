from pydicom import Dataset

from dimsechord.multipart import build_multipart_response, extract_frames_from_dataset


def test_extract_single_frame_returns_all_pixeldata_per_request() -> None:
    ds = Dataset()
    ds.PixelData = b"ABCD"
    assert extract_frames_from_dataset(ds, [1]) == [b"ABCD"]


def test_extract_multiframe_splits_evenly() -> None:
    ds = Dataset()
    ds.NumberOfFrames = 2
    ds.PixelData = b"AABB"
    assert extract_frames_from_dataset(ds, [1, 2]) == [b"AA", b"BB"]


def test_extract_no_pixeldata_returns_empty() -> None:
    assert extract_frames_from_dataset(Dataset(), [1]) == []


def test_build_multipart_response_shape() -> None:
    body, content_type = build_multipart_response([b"AA", b"BB"])
    assert content_type.startswith('multipart/related; type="application/octet-stream"; boundary=')
    boundary = content_type.rsplit("boundary=", 1)[1]
    assert body.count(f"--{boundary}\r\n".encode()) == 2
    assert body.endswith(f"--{boundary}--\r\n".encode())
    assert b"AA" in body and b"BB" in body
