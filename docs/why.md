# Why dimsechord?

[`pydicom`](https://github.com/pydicom/pydicom) and
[`pynetdicom`](https://github.com/pydicom/pynetdicom) are excellent libraries,
but they are low-level by design. To make a single C-FIND query you build an
`AE`, register presentation contexts, open an association, iterate
`(status, identifier)` pairs, and release the association — and that is before
you have dealt with retrieval, caching, or turning datasets into DICOMweb JSON.

`dimsechord` is a small, stable, typed API of composable building blocks over
that surface. It gives you DIMSE networking, a streaming retrieval engine, a
two-tier cache, and DICOM↔DICOMweb conversion — and deliberately stops there.
There is no bundled web framework: you bring your own.

This page walks through the concrete problem each part solves.

## DIMSE boilerplate

A query is one typed call:

```python
from dimsechord import DicomClient, DicomNode, StudyQuery

client = DicomClient(calling_aet="MYSCU")
pacs = DicomNode(aet="PACS", host="127.0.0.1", port=11112)
studies = await client.find_studies(StudyQuery(patient_id="12345"), pacs)
```

`studies` is a `list[StudyResult]` — typed fields, not attribute lookups on a
raw `Dataset`. The raw-`pynetdicom` equivalent means constructing an `AE`,
adding the Study Root Find presentation context, associating, looping over
response identifiers, mapping DICOM keywords to values by hand, and releasing
the association. `DicomClient` wraps C-FIND, C-STORE, C-MOVE, and C-GET behind
that one-call shape. See the [cookbook](cookbook.md#query-c-find).

## Move-to-self retrieval

C-MOVE does not return instances to the requester. It tells the PACS to *push*
them to a named destination AE title. To retrieve to yourself you must:

- run a Storage SCP to receive the incoming C-STORE sub-operations;
- get the PACS configured to route that destination AET back to your SCP's host
  and port;
- coordinate the move thread with the receive queue so you can stream instances
  as they land.

`PullEngine` drives all of that and yields instances one by one. If you would
rather not run an SCP at all, **C-GET** retrieves on a single association —
`PullEngine.via_cget`, or `DicomClient.get_*`. See
[retrieve](cookbook.md#retrieve-c-move-vs-c-get) and
[streaming pull](cookbook.md#streaming-pull-with-cache).

## Sync vs async impedance

`pynetdicom` is synchronous and thread-based; a web service is `async`.
dimsechord keeps a synchronous, thread-safe core — so it also works inside a
DIMSE worker thread, which cannot `await` — and exposes thin `async` adapters on
top. The HTTP face gets `async for ds in engine.stream_series(...)` without
re-implementing the thread-to-event-loop bridge.

## Serving retrieved data

Once instances arrive you usually need to serve them. dimsechord includes:

- a **two-tier cache** (in-memory + on-disk) backed by a SQLite instance index,
  so a series pulled for one request is available to the next;
- **DICOM ↔ DICOMweb JSON** conversion (`convert_datasets_to_dicom_json` and the
  QIDO result converters);
- **`multipart/related`** frame responses for WADO-RS.

See [DICOMweb JSON](cookbook.md#dicomweb-json) and
[frames](cookbook.md#multipart-frames).

## Multiple AE identities

Some deployments rotate among several calling/destination AE titles, each with
its own concurrency cap. `AssociationPool` leases one identity at a time and
blocks — or raises — when every slot is busy. See
[the pool recipe](cookbook.md#multiple-ae-identities).

## What dimsechord is not

- **Not a web framework.** It has no HTTP layer; wire it into
  Starlette/FastAPI/anything (see the [gateway tutorial](tutorial-gateway.md)).
- **Not an anonymizer.** It moves and converts datasets; it does not
  de-identify them.
- **Not a full PACS.** It is the client/SCU side plus a receiving SCP, not an
  archive.

## Next

- [Typing](typing.md) — how the typed API works with mypy/pyright.
- [Cookbook](cookbook.md) — one recipe per feature.
- [Gateway tutorial](tutorial-gateway.md) — an end-to-end DICOMweb gateway on
  Starlette.
