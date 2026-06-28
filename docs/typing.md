# Typing

`dimsechord` ships fully typed. It includes a `py.typed` marker
([PEP 561](https://peps.python.org/pep-0561/)) and the `Typing :: Typed`
classifier, so mypy and pyright pick up its types with zero configuration. Every
query and result is annotated — you get autocomplete and type errors before you
run anything.

## Typed query → result pairs

Each Query/Retrieve level has its own query and result type:

```python
from dimsechord import DicomClient, DicomNode, StudyQuery, StudyResult

client = DicomClient(calling_aet="MYSCU")
pacs = DicomNode(aet="PACS", host="127.0.0.1", port=11112)

results: list[StudyResult] = await client.find_studies(StudyQuery(modality="CT"), pacs)
for r in results:
    uid: str = r.study_instance_uid
    n: int | None = r.number_of_study_related_instances
```

- `StudyQuery → list[StudyResult]`
- `SeriesQuery → list[SeriesResult]`
- `ImageQuery → list[ImageResult]`

The typed result replaces poking attributes on a raw `pydicom.Dataset`, where
every field is `Any` and a misspelled keyword is a runtime `AttributeError`
instead of an error your editor flags as you type.

## Construction is keyword-only and typo-safe

The query and result models are `@dataclass(slots=True, kw_only=True)`.
Construction is keyword-only, so a typo or a misplaced positional argument fails
loudly rather than silently doing the wrong thing:

```python
from dimsechord import SeriesQuery

q = SeriesQuery(study_instance_uid="1.2.3", modality="CT")
# SeriesQuery("1.2.3")             -> TypeError: positional arguments not allowed
# SeriesQuery(studyinstanceuid=1)  -> TypeError: unexpected keyword argument 'studyinstanceuid'
```

`slots=True` also means assigning an undefined attribute raises instead of
quietly creating a misspelled field. Note that dataclasses do not coerce: pass
correctly-typed values (`port=11112`, not `port="11112"`).

## StrEnum levels

`QueryRetrieveLevel` is a `StrEnum`, so its members are strings and compare
equal to their wire value — pass a member or a plain string, whichever reads
better:

```python
from dimsechord import QueryRetrieveLevel

assert QueryRetrieveLevel.STUDY == "STUDY"
```

## Typed DICOMweb output

Conversion returns a typed mapping, not `dict[str, Any]`:

```python
from dimsechord import DicomJson, study_result_to_dicom_json

js: DicomJson = study_result_to_dicom_json(results[0])
# js["0020000D"] == {"vr": "UI", "Value": ["1.2.3"]}
```

`DicomJson` is `dict[str, TagValue]`; `TagValue` is a `TypedDict` carrying `vr`
plus an optional `Value`, `BulkDataURI`, or `InlineBinary`. Your type checker
knows the shape of a DICOMweb response, so building or consuming one is checked.

## The public surface is the stability contract {#stability}

The supported API is exactly the names in `dimsechord.__all__` — everything you
can `from dimsechord import`. Modules with a leading underscore
(`dimsechord._scu`, `dimsechord._cache`, …) are private and may change without
notice. That public, typed surface is the stability contract; it is frozen by a
test (`tests/unit/test_public_api.py`) so it cannot drift accidentally. If you
need something that is not exported, open an issue to extend the surface rather
than importing a private symbol.
