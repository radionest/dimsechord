import logging

from pydicom import Dataset

from dimsechord._converter import (
    convert_datasets_to_dicom_json,
    dataset_to_dicom_json,
    image_result_to_dicom_json,
    series_result_to_dicom_json,
    study_result_to_dicom_json,
)
from dimsechord._models import ImageResult, SeriesResult, StudyResult


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


def test_study_result_extended_fields_to_dicom_json() -> None:
    result = StudyResult(
        study_instance_uid="1.2.3",
        patient_birth_date="19700101",
        patient_sex="M",
        study_id="S1",
        referring_physician_name="SMITH^JOHN",
        institution_name="HOSP",
        station_name="STN1",
        sop_classes_in_study=["1.2.3", "1.2.4"],
    )
    js = study_result_to_dicom_json(result)
    assert js["00100030"] == {"vr": "DA", "Value": ["19700101"]}
    assert js["00100040"] == {"vr": "CS", "Value": ["M"]}
    assert js["00200010"] == {"vr": "SH", "Value": ["S1"]}
    assert js["00080090"] == {"vr": "PN", "Value": [{"Alphabetic": "SMITH^JOHN"}]}
    assert js["00080080"] == {"vr": "LO", "Value": ["HOSP"]}
    assert js["00081010"] == {"vr": "SH", "Value": ["STN1"]}
    assert js["00080062"] == {"vr": "UI", "Value": ["1.2.3", "1.2.4"]}


def test_series_result_extended_fields_to_dicom_json() -> None:
    result = SeriesResult(
        study_instance_uid="1.2.3",
        series_instance_uid="1.2.3.4",
        body_part_examined="BRAIN",
        protocol_name="PROT1",
        series_date="20200101",
        operator_name=["OPER^X", "OPER^Y"],
        performed_procedure_step_description="PPS desc",
    )
    js = series_result_to_dicom_json(result)
    assert js["00180015"] == {"vr": "CS", "Value": ["BRAIN"]}
    assert js["00181030"] == {"vr": "LO", "Value": ["PROT1"]}
    assert js["00080021"] == {"vr": "DA", "Value": ["20200101"]}
    assert js["00081070"] == {
        "vr": "PN",
        "Value": [{"Alphabetic": "OPER^X"}, {"Alphabetic": "OPER^Y"}],
    }
    assert js["00400253"] == {"vr": "LO", "Value": ["PPS desc"]}


def test_image_result_extended_fields_to_dicom_json() -> None:
    result = ImageResult(
        study_instance_uid="1.2.3",
        series_instance_uid="1.2.3.4",
        sop_instance_uid="1.2.3.4.1",
        image_type=["ORIGINAL", "PRIMARY"],
        content_date="20200101",
        slice_thickness=2.5,
    )
    js = image_result_to_dicom_json(result)
    assert js["00080008"] == {"vr": "CS", "Value": ["ORIGINAL", "PRIMARY"]}
    assert js["00080023"] == {"vr": "DA", "Value": ["20200101"]}
    assert js["00180050"] == {"vr": "DS", "Value": [2.5]}


def test_extended_none_fields_omitted_from_dicom_json() -> None:
    js = study_result_to_dicom_json(StudyResult(study_instance_uid="1.2.3"))
    for tag in ("00100030", "00100040", "00200010", "00080090", "00080080", "00081010", "00080062"):
        assert tag not in js
