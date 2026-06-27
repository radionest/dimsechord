import logging

from pydicom import Dataset

from dimsechord._converter import (
    convert_datasets_to_dicom_json,
    dataset_to_dicom_json,
    study_result_to_dicom_json,
)
from dimsechord._models import StudyResult


def test_study_result_to_dicom_json_tags_and_pn() -> None:
    result = StudyResult(
        study_instance_uid="1.2.3",
        patient_id="P1",
        patient_name="DOE^JOHN",
        modalities_in_study="CT\\SR",
    )
    js = study_result_to_dicom_json(result)
    assert js["0020000D"] == {"vr": "UI", "Value": ["1.2.3"]}
    assert js["00100010"] == {"vr": "PN", "Value": [{"Alphabetic": "DOE^JOHN"}]}
    # multi-value modalities split back into a JSON array
    assert js["00080061"] == {"vr": "CS", "Value": ["CT", "SR"]}


def test_study_result_omits_none_fields() -> None:
    js = study_result_to_dicom_json(StudyResult(study_instance_uid="1.2.3"))
    assert "00100020" not in js  # PatientID absent


def test_dataset_to_dicom_json_sets_bulkdata_uri_for_pixeldata() -> None:
    ds = Dataset()
    ds.StudyInstanceUID = "1.2"
    ds.SeriesInstanceUID = "1.2.3"
    ds.SOPInstanceUID = "1.2.3.4"
    ds.PixelData = b"\x00\x01" * 8
    js = dataset_to_dicom_json(ds, base_url="http://x/dicom-web")
    assert js["7FE00010"]["vr"] == "OW"
    assert js["7FE00010"]["BulkDataURI"].endswith(
        "/studies/1.2/series/1.2.3/instances/1.2.3.4/frames/1"
    )
    assert "Value" not in js["7FE00010"]  # pixel data not inlined


def test_convert_datasets_skips_unreadable(caplog) -> None:
    good = Dataset()
    good.StudyInstanceUID = "1.2"
    good.SeriesInstanceUID = "1.2.3"
    good.SOPInstanceUID = "1.2.3.4"

    class _Unreadable:
        def get(self, *_args, **_kwargs):
            return ""

        def keys(self):
            raise RuntimeError("corrupt dataset")

    with caplog.at_level(logging.WARNING):
        out = convert_datasets_to_dicom_json([good, _Unreadable()], base_url="http://x")

    assert len(out) == 1
    assert "Skipping unreadable" in caplog.text
