from pydicom import Dataset
from pydicom.multival import MultiValue
from pydicom.uid import UID

from dimsechord._models import ImageQuery, SeriesQuery, StudyQuery
from dimsechord._scu import DicomOperations, _ds_float, _ds_str_list


def _ds(**kw) -> Dataset:
    ds = Dataset()
    for k, v in kw.items():
        setattr(ds, k, v)
    return ds


# ── _ds_float ────────────────────────────────────────────────────
def test_ds_float_numeric() -> None:
    assert _ds_float(_ds(SliceThickness="2.5"), "SliceThickness") == 2.5


def test_ds_float_missing_returns_none() -> None:
    assert _ds_float(Dataset(), "SliceThickness") is None


def test_ds_float_empty_returns_none() -> None:
    assert _ds_float(_ds(SliceThickness=""), "SliceThickness") is None


def test_ds_float_non_numeric_returns_none(caplog) -> None:
    import logging

    # _ds_float is VR-agnostic (getattr + float). pydicom validates DS on
    # assignment, so we cannot seed a non-numeric SliceThickness directly;
    # use a non-DS attribute holding a non-numeric string to exercise the
    # same defensive branch a malformed DS would hit when decoded off-wire.
    with caplog.at_level(logging.WARNING):
        assert _ds_float(_ds(PatientID="not-a-number"), "PatientID") is None
    assert "PatientID" in caplog.text


# ── _ds_str_list ─────────────────────────────────────────────────
def test_ds_str_list_multivalue() -> None:
    ds = _ds(SOPClassesInStudy=MultiValue(UID, ["1.2.3", "1.2.4"]))
    assert _ds_str_list(ds, "SOPClassesInStudy") == ["1.2.3", "1.2.4"]


def test_ds_str_list_single_string() -> None:
    assert _ds_str_list(_ds(ImageType="ORIGINAL"), "ImageType") == ["ORIGINAL"]


def test_ds_str_list_missing_returns_none() -> None:
    assert _ds_str_list(Dataset(), "ImageType") is None


def test_ds_str_list_empty_returns_none() -> None:
    assert _ds_str_list(_ds(ImageType=""), "ImageType") is None


# ── query builders request the new retrieval keys ────────────────
def test_study_query_requests_extended_keys_as_empty_retrieval() -> None:
    ds = DicomOperations(calling_aet="X")._build_study_query_dataset(StudyQuery())
    for attr in (
        "PatientBirthDate",
        "PatientSex",
        "StudyID",
        "ReferringPhysicianName",
        "InstitutionName",
        "StationName",
        "SOPClassesInStudy",
    ):
        assert getattr(ds, attr) == ""  # empty retrieval key


def test_series_query_requests_extended_keys() -> None:
    ds = DicomOperations(calling_aet="X")._build_series_query_dataset(
        SeriesQuery(study_instance_uid="1.2.3")
    )
    for attr in (
        "BodyPartExamined",
        "ProtocolName",
        "SeriesDate",
        "OperatorsName",
        "PerformedProcedureStepDescription",
    ):
        assert getattr(ds, attr) == ""


def test_image_query_requests_extended_keys() -> None:
    ds = DicomOperations(calling_aet="X")._build_image_query_dataset(
        ImageQuery(study_instance_uid="1.2.3", series_instance_uid="1.2.3.4")
    )
    for attr in ("ImageType", "ContentDate", "SliceThickness"):
        assert getattr(ds, attr) == ""


# ── parsers populate the new fields ──────────────────────────────
def test_parse_study_result_populates_extended_fields() -> None:
    ds = _ds(
        StudyInstanceUID="1.2.3",
        PatientBirthDate="19700101",
        PatientSex="M",
        StudyID="S1",
        ReferringPhysicianName="SMITH^JOHN",
        InstitutionName="HOSP",
        StationName="STN1",
        SOPClassesInStudy=MultiValue(UID, ["1.2.3", "1.2.4"]),
    )
    r = DicomOperations(calling_aet="X")._parse_study_result(ds)
    assert r.patient_birth_date == "19700101"
    assert r.patient_sex == "M"
    assert r.study_id == "S1"
    assert r.referring_physician_name == "SMITH^JOHN"
    assert r.institution_name == "HOSP"
    assert r.station_name == "STN1"
    assert r.sop_classes_in_study == ["1.2.3", "1.2.4"]


def test_parse_series_result_populates_extended_fields() -> None:
    ds = _ds(
        StudyInstanceUID="1.2.3",
        SeriesInstanceUID="1.2.3.4",
        BodyPartExamined="BRAIN",
        ProtocolName="PROT1",
        SeriesDate="20200101",
        OperatorsName="OPER^X",
        PerformedProcedureStepDescription="PPS desc",
    )
    r = DicomOperations(calling_aet="X")._parse_series_result(ds)
    assert r.body_part_examined == "BRAIN"
    assert r.protocol_name == "PROT1"
    assert r.series_date == "20200101"
    assert r.operator_name == "OPER^X"
    assert r.performed_procedure_step_description == "PPS desc"


def test_parse_image_result_populates_extended_fields() -> None:
    ds = _ds(
        StudyInstanceUID="1.2.3",
        SeriesInstanceUID="1.2.3.4",
        SOPInstanceUID="1.2.3.4.1",
        ImageType=MultiValue(str, ["ORIGINAL", "PRIMARY"]),
        ContentDate="20200101",
        SliceThickness="2.5",
    )
    r = DicomOperations(calling_aet="X")._parse_image_result(ds)
    assert r.image_type == ["ORIGINAL", "PRIMARY"]
    assert r.content_date == "20200101"
    assert r.slice_thickness == 2.5
