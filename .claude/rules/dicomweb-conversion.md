---
paths:
  - "src/dimsechord/_converter.py"
  - "src/dimsechord/_multipart.py"
---

# DICOMweb conversion (DICOM JSON + WADO-RS)

`_converter.py` and `_multipart.py` are presentation-layer utilities — no
DIMSE networking here, only format conversion.

## `_converter.py`

- Converts to **DICOM JSON** per the DICOMweb standard, **PS3.18
  Appendix F**.
- Accepts both raw pydicom `Dataset` objects (`dataset_to_dicom_json`,
  `convert_datasets_to_dicom_json`) and this package's own dataclass
  results — `study_result_to_dicom_json`, `series_result_to_dicom_json`,
  `image_result_to_dicom_json` take `StudyResult`/`SeriesResult`/
  `ImageResult` (`_models.py`).
- When adding a new field to a `*Result` dataclass that should appear in
  QIDO-RS responses, the corresponding `*_result_to_dicom_json` function
  needs updating too — they are not auto-derived.

## `_multipart.py`

- Implements **WADO-RS** frame retrieval: `extract_frames_from_dataset`
  pulls specific frame numbers out of a (possibly multi-frame)
  `PixelData`; `build_multipart_response` assembles the
  `multipart/related` HTTP body from the extracted frames.
