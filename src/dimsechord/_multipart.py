"""Multipart response builder for WADO-RS frame retrieval."""

import uuid

from pydicom import Dataset


def extract_frames_from_dataset(ds: Dataset, frame_numbers: list[int]) -> list[bytes]:
    if not hasattr(ds, "PixelData"):
        return []

    pixel_data = ds.PixelData
    number_of_frames = int(getattr(ds, "NumberOfFrames", 1))

    if number_of_frames == 1:
        # Single-frame image — return entire pixel data for any requested frame
        return [pixel_data] * len(frame_numbers)

    # Multi-frame: split pixel data evenly
    frame_size = len(pixel_data) // number_of_frames
    frames: list[bytes] = []
    for frame_num in frame_numbers:
        if 1 <= frame_num <= number_of_frames:
            start = (frame_num - 1) * frame_size
            end = start + frame_size
            frames.append(pixel_data[start:end])

    return frames


def build_multipart_response(frames: list[bytes]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    content_type = f'multipart/related; type="application/octet-stream"; boundary={boundary}'

    parts: list[bytes] = []
    for frame_data in frames:
        part = (
            (
                f"--{boundary}\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Length: {len(frame_data)}\r\n"
                f"\r\n"
            ).encode()
            + frame_data
            + b"\r\n"
        )
        parts.append(part)

    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return body, content_type
