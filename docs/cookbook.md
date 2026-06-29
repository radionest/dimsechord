# Cookbook

One focused recipe per feature. Every snippet uses only the public API
(`from dimsechord import …`). The client-based recipes share this setup:

```python
from dimsechord import DicomClient, DicomNode

pacs = DicomNode(aet="PACS", host="127.0.0.1", port=11112)
client = DicomClient(calling_aet="MYSCU")
```

- [Query (C-FIND)](#query-c-find)
- [Store to a peer (C-STORE)](#store-to-a-peer-c-store)
- [Retrieve (C-MOVE vs C-GET)](#retrieve-c-move-vs-c-get)
- [Receive instances (C-STORE SCP)](#receive-instances-c-store-scp)
- [Streaming pull with cache](#streaming-pull-with-cache)
- [DICOMweb JSON](#dicomweb-json)
- [Multipart frames](#multipart-frames)
- [Multiple AE identities](#multiple-ae-identities)
- [Error handling](#error-handling)

## Query (C-FIND)

Find studies, then series, then images — each level has its own typed query:

```python
from dimsechord import StudyQuery, SeriesQuery, ImageQuery

studies = await client.find_studies(StudyQuery(patient_id="12345"), pacs)
series = await client.find_series(
    SeriesQuery(study_instance_uid=studies[0].study_instance_uid), pacs)
images = await client.find_images(
    ImageQuery(
        study_instance_uid=studies[0].study_instance_uid,
        series_instance_uid=series[0].series_instance_uid,
    ),
    pacs,
)
```

`find_studies` returns `list[StudyResult]`, `find_series` returns
`list[SeriesResult]`, `find_images` returns `list[ImageResult]`.

## Store to a peer (C-STORE)

Send one dataset, or a batch over a single association:

```python
from pydicom import dcmread

ok: bool = await client.store_instance(dcmread("image.dcm"), pacs)

result = await client.store_instances_batch(
    [dcmread("a.dcm"), dcmread("b.dcm")], pacs)
print(result.total_sent, result.total_failed, result.failed_sop_uids)
```

`store_instance` returns a `bool`; `store_instances_batch` returns a
`BatchStoreResult` summarizing the batch.

## Retrieve (C-MOVE vs C-GET)

C-MOVE asks the PACS to push instances to a destination AE title (a Storage SCP
you run). C-GET delivers them on the same association, with nothing else to run:

```python
from pathlib import Path

# C-MOVE: the PACS pushes to the destination AET.
moved = await client.move_study(study_uid, pacs, destination_aet="MYDEST")

# C-GET: instances arrive on this association.
got = await client.get_study(study_uid, pacs, output_dir=Path("./out"))   # to disk
mem = await client.get_series_to_memory(study_uid, series_uid, pacs)       # to memory
print(moved.num_completed, got.num_completed, len(mem.instances))
```

All three return a `RetrieveResult` (`status`, `num_completed`, `num_failed`,
…). Memory-mode C-GET also populates `RetrieveResult.instances`
(`sop_uid → Dataset`). Reach for C-GET when you cannot run or route an SCP; use
C-MOVE when the PACS only supports move, or you want it to push to a separate
destination.

## Receive instances (C-STORE SCP)

`StorageSCP` listens for incoming C-STORE sub-operations. It accepts requests
addressed to any called AE title, so every AET in a pool routes to it:

```python
from dimsechord import StorageSCP

scp = StorageSCP()
scp.start({"MYDEST": 11113})
try:
    ...  # the SCP feeds per-request queues; drive it with PullEngine (next recipe)
finally:
    scp.stop()
```

On its own the SCP just receives; pair it with
[`PullEngine`](#streaming-pull-with-cache) to turn arrivals into a stream.

## Streaming pull with cache

`PullEngine` is the move-to-self retrieve path: a cache miss triggers a C-MOVE
to your `StorageSCP`, and instances are streamed and teed into the cache as they
arrive. A later request for the same series is served from memory or disk.

```python
from dimsechord import AssociationPool, DicomCache, PullEngine, StorageSCP

pool = AssociationPool(aets=["MYDEST"])
scp = StorageSCP()
scp.start({aet: 11113 for aet in pool.aets})
# The PACS must be configured to route the AET "MYDEST" back to this SCP's host:port.
cache = DicomCache(base_dir="/var/cache/dimsechord",
                   index_path="/var/cache/dimsechord/index.db")
engine = PullEngine(pool=pool, scp=scp, cache=cache, pacs=pacs)
try:
    async for ds in engine.stream_series(study_uid, series_uid):
        ...                                   # miss → C-MOVE-to-self; hit → from cache
    cached = await engine.ensure_series(study_uid, series_uid)   # -> MemoryCachedSeries
    print(len(cached.instances))
finally:
    scp.stop()
    cache.shutdown()
```

To retrieve via **C-GET** instead, build the engine with `via_cget` — no pool,
no SCP, no PACS routing:

```python
engine = PullEngine.via_cget(cache=cache, pacs=pacs, calling_aet="MYGETSCU")
cached = await engine.ensure_series(study_uid, series_uid)
```

Use `stream_series` / `stream_study` for `async for`, or `ensure_series` to
materialize the whole series as a `MemoryCachedSeries`.

### Multi-port: one endpoint per AET

Real PACS systems often require each registered AE title to have its own
distinct host:port. Bind one listener per AET by giving each a different port:

```python
pool = AssociationPool(aets=["DEST_A", "DEST_B"])
scp = StorageSCP()
scp.start({"DEST_A": 11113, "DEST_B": 11114})  # one listener per AET
```

## DICOMweb JSON

Convert C-FIND results into QIDO-style JSON, or retrieved datasets into WADO-RS
metadata:

```python
from dimsechord import convert_datasets_to_dicom_json, study_result_to_dicom_json

# QIDO: one JSON object per C-FIND result.
qido = [study_result_to_dicom_json(s) for s in studies]

# WADO metadata: datasets -> JSON (BulkDataURIs built from base_url).
metadata = convert_datasets_to_dicom_json(
    list(cached.instances.values()), base_url="https://example.org/dicom-web")
# metadata[0]["0020000D"] == {"vr": "UI", "Value": ["1.2.3"]}
```

The result converters (`study_result_to_dicom_json`,
`series_result_to_dicom_json`, `image_result_to_dicom_json`) take just the
result object. `convert_datasets_to_dicom_json` and `dataset_to_dicom_json` take
datasets plus a required `base_url`, used to build bulk-data URIs.

## Multipart frames

Extract pixel frames and wrap them in a `multipart/related` body for WADO-RS:

```python
from dimsechord import extract_frames_from_dataset, build_multipart_response

frames = extract_frames_from_dataset(ds, frame_numbers=[1, 2])   # 1-based -> list[bytes]
body, content_type = build_multipart_response(frames)
# content_type == 'multipart/related; type="application/octet-stream"; boundary=...'
# Serve `body` with header  Content-Type: {content_type}
```

Frame numbers are **1-based** (the WADO-RS convention). A single-frame instance
returns its whole pixel data for each requested number; an instance with no
pixel data returns an empty list. `build_multipart_response` returns
`(body, content_type)` — hand both straight to your HTTP response.

## Multiple AE identities

`AssociationPool` leases one of several AE titles, with a per-AET concurrency
cap:

```python
from dimsechord import AssociationPool, PoolExhaustedError

pool = AssociationPool(aets=["DEST1", "DEST2"], per_aet_cap=2)   # 2 AETs × cap 2 = 4 slots
print(pool.aets, pool.total_capacity)
try:
    with pool.lease(timeout=5.0) as aet:
        ...                       # use `aet` as the calling / C-MOVE destination AE title
except PoolExhaustedError:
    ...                           # no slot freed within the timeout
```

`lease` is a context manager: the slot is released when the block exits.

## Error handling

All errors derive from `DimsechordError`, so you can catch the base class or a
specific failure:

```python
from dimsechord import (
    DimsechordError, ArrivalTimeoutError, MoveToSelfError,
    AssociationError, PoolExhaustedError,
)

try:
    async for ds in engine.stream_series(study_uid, series_uid):
        ...
except MoveToSelfError:
    ...      # C-MOVE finished but nothing arrived — the PACS isn't routing the AET back
except ArrivalTimeoutError:
    ...      # the gap between two instances exceeded arrival_timeout
except DimsechordError:
    ...      # base class — catch-all
```

| Exception | Raised when |
| --- | --- |
| `AssociationError` | an association or C-GET/C-STORE sub-operation fails |
| `PoolExhaustedError` | `AssociationPool.lease` times out with no free slot |
| `MoveToSelfError` | a C-MOVE completes but zero instances arrive |
| `ArrivalTimeoutError` | no instance arrives within the configured `arrival_timeout` |
