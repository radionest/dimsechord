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
- [Relay stores over one association (StoreSession)](#relay-stores-over-one-association-storesession)
- [Forward compressed instances (presentation contexts for a proxy SCU)](#forward-compressed-instances-presentation-contexts-for-a-proxy-scu)
- [Retrieve (C-MOVE vs C-GET)](#retrieve-c-move-vs-c-get)
- [Receive instances (C-STORE SCP)](#receive-instances-c-store-scp)
- [Streaming pull with cache](#streaming-pull-with-cache)
- [Streaming find (raw C-FIND pass-through)](#streaming-find-raw-c-find-pass-through)
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
`list[SeriesResult]`, `find_images` returns `list[ImageResult]`. Beyond the
identifying UIDs, each also carries standard optional DICOM fields (e.g.
`patient_birth_date` / `referring_physician_name` / `sop_classes_in_study`
on `StudyResult`, `operator_name` / `body_part_examined` on `SeriesResult`,
`image_type` / `slice_thickness` on `ImageResult`) — the dataclasses are
typed, so your editor/type checker shows the full field list.

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

## Relay stores over one association (StoreSession)

`StoreSession` holds one persistent association open across many `store`
calls, for a relay that forwards each inbound instance upstream without
renegotiating a fresh association per instance. `store` passes the peer's
DIMSE status through verbatim as a plain `int` — success and warning are
both just values to branch on, never exceptions:

```python
from pydicom import dcmread

from dimsechord import DicomNode, StoreSession

archive = DicomNode(aet="ARCHIVE", host="pacs.example.org", port=104)

with StoreSession(archive, calling_aet="PROXY") as session:
    status: int = session.store(dcmread("image.dcm"))
    if status == 0x0000:
        ...                 # success
    elif status == 0xB000:
        ...                 # warning (e.g. coercion of data elements) — still delivered
    else:
        ...                 # non-success status — handle per your relay's policy
```

A per-instance presentation-context miss raises `NoPresentationContextError`
before anything is sent, leaving the association open and usable for the
next `store` — a relay typically maps it to DIMSE status `0x0122` in its own
response to the sender.

A dropped association reopens transparently only when nothing was provably
sent yet; a failure discovered mid-`send_c_store` is ambiguous instead, so
`store` raises `AssociationError` and never auto-resends that instance.

Always `close()` the session, or use the context manager as above: a relay
association is idle by design, so `StoreSession` disables pynetdicom's
default 60 s idle `network_timeout` — meaning a leaked session never
self-terminates and keeps its association open. `StoreSession` also does
not draw from `DicomClient.set_max_concurrent_associations`'s global cap —
callers are responsible for bounding session concurrency themselves.

## Forward compressed instances (presentation contexts for a proxy SCU)

A proxy must forward objects verbatim. pynetdicom converts freely between
uncompressed transfer syntaxes, but a compressed dataset needs an exactly
matching accepted context — one context per (SOP class, syntax) pair, within
the DICOM limit of 128 per association. `build_storage_scu_contexts()` builds
a curated set that fits:

```python
from pynetdicom import AE

from dimsechord import build_storage_scu_contexts

ae = AE(ae_title="PROXY")
ae.requested_contexts = build_storage_scu_contexts()
assoc = ae.associate("pacs.example.org", 104, ae_title="ARCHIVE")
# assoc.send_c_store(ds) now finds an exact context for compressed datasets.
```

Tune the defaults to your traffic — every parameter is optional:

```python
from dimsechord import (
    DEFAULT_COMPRESSED_TRANSFER_SYNTAXES,
    DEFAULT_IMAGE_STORAGE_CLASSES,
    build_storage_scu_contexts,
)

contexts = build_storage_scu_contexts(
    image_classes=[*DEFAULT_IMAGE_STORAGE_CLASSES, "1.2.840.10008.5.1.4.1.1.2.2"],
    compressed_syntaxes=[*DEFAULT_COMPRESSED_TRANSFER_SYNTAXES, "1.2.840.10008.1.2.4.201"],
)
```

The defaults produce 106 contexts, leaving headroom under the 128 limit;
`ValueError` is raised if a customization overflows it. On the receive side no
tuning is needed: `StorageSCP` accepts every transfer syntax by default.

A proxy's own inbound side — built directly with pynetdicom's `AE` rather
than `StorageSCP` — can instead accept exactly the curated matrix, with
`build_storage_scp_contexts()`:

```python
from dimsechord import build_storage_scp_contexts

for cx in build_storage_scp_contexts():
    ae.add_supported_context(cx.abstract_syntax, cx.transfer_syntax)
```

The two builders mirror for identical arguments: whatever such an SCP
accepts, a `StoreSession` built from the same matrix can always propose
upstream.

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

## Streaming find (raw C-FIND pass-through)

`QueryEngine` is symmetric to `PullEngine`, but for C-FIND instead of
retrieval: it leases a find identity from an `AssociationPool` per query and
streams *raw* response identifiers — no typed parsing, so the identifier you
build goes out unmodified and each response comes back exactly as the peer
sent it. `iter_find` is sync (for a DIMSE SCP thread); `stream_find` is its
async counterpart:

```python
from pydicom import Dataset
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind as FIND

from dimsechord import AssociationPool, QueryEngine

pool = AssociationPool(aets=["MYFIND"], per_aet_find_cap=4)
engine = QueryEngine(pool=pool, pacs=pacs)

identifier = Dataset()
identifier.QueryRetrieveLevel = "STUDY"
identifier.PatientName = "DOE^JOHN"
identifier.StudyInstanceUID = ""

async for ds in engine.stream_find(identifier, model=FIND):
    ...                                    # ds: raw pending-response Dataset
```

The `model=` argument is the caller's choice: a Patient-Root-only peer is
served by passing `PatientRootQueryRetrieveInformationModelFind` with a
patient-rooted identifier — the typed face itself is Study Root only.
(Queries only: the raw face has no retrieve counterpart.)

Find leases (`per_aet_find_cap`, default 4) are a cap independent of move
leases (`per_aet_cap`) on the same pool — `lease_find` neither blocks nor is
blocked by `lease`/C-MOVE-to-self traffic, matching a PACS's tolerance for
several concurrent C-FIND associations per AET. The lease is acquired
lazily: `iter_find`/`stream_find` only build the generator, and the lease —
with any `PoolExhaustedError` — surfaces on the first `next()`/`anext()`,
not at the call itself. A non-success final DIMSE
status raises `FindFailedError`, carrying the status code as `.status`:

```python
from dimsechord import FindFailedError

try:
    async for ds in engine.stream_find(identifier, model=FIND):
        ...
except FindFailedError as e:
    ...                                    # e.status, e.g. 0xA700
```

Convert each raw response to QIDO JSON with `dataset_to_qido_json` — unlike
`dataset_to_dicom_json` it injects no `BulkDataURI` (C-FIND responses carry
no pixel data):

```python
from dimsechord import dataset_to_qido_json

qido = [dataset_to_qido_json(ds) async for ds in engine.stream_find(identifier, model=FIND)]
```

`stream_find` and `PullEngine`'s `stream_series`/`stream_study` all run on
`iter_to_aiter`, the shared sync-iterator→async-iterator bridge: a worker
thread drives the sync generator and feeds a bounded queue, so a slow async
consumer parks the producer thread and the bridge never buffers more than
its queue bound. The bound is per-bridge, not end-to-end: pynetdicom's DUL
thread still reads incoming PDUs at line speed into an unbounded internal
queue, so it caps this library's buffering, not the peer's send rate.
Closing the async generator early
aborts the upstream association instead of draining the remaining responses:
an explicit `aclose()` releases the pool lease before it returns, while a
merely abandoned generator is finalized by the event loop shortly after.

If the deployment opted into a global cap via
`DicomClient.set_max_concurrent_associations`, that permit is held for the
*entire* stream, not just while associating — a slow or long-lived
`stream_find`/`stream_series`/`stream_study` consumer occupies one of those
global slots for as long as it keeps iterating.

Each live stream also pins one thread in asyncio's default executor
(`asyncio.to_thread`; pool size `min(32, cpu_count + 4)`) for its whole
lifetime. Budget concurrent `stream_*` consumers — AETs ×
`per_aet_find_cap`, plus pulls, plus your application's own `to_thread`
work — against that pool, or new `anext()` calls quietly queue until a
worker frees up.

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

pool = AssociationPool(aets=["DEST1", "DEST2"], per_aet_cap=2)   # 2 AETs × cap 2 = 4 move slots
print(pool.aets, pool.total_capacity)   # total_capacity counts move slots only, not find slots
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
    AssociationError, FindFailedError, PoolExhaustedError,
)

try:
    async for ds in engine.stream_series(study_uid, series_uid):
        ...
except MoveToSelfError:
    ...      # C-MOVE matched nothing — it completed reporting zero sub-operations
             # (routing misconfig, where reported instances never arrive, is AssociationError)
except ArrivalTimeoutError:
    ...      # the gap between two instances exceeded arrival_timeout
except DimsechordError:
    ...      # base class — catch-all
```

| Exception | Raised when |
| --- | --- |
| `AssociationError` | an association fails (including a peer that refuses a needed presentation context — the typed face requires Study Root Q/R), or a C-GET/C-STORE/C-MOVE sub-operation fails — including a C-MOVE or C-GET that ends on a non-success status, or a C-MOVE that under-delivers |
| `FindFailedError` | a raw streaming C-FIND (`iter_find`/`stream_find`) ends with a non-success DIMSE status |
| `PoolExhaustedError` | `AssociationPool.lease`, used directly — or `lease_find`, on the first iteration of `iter_find`/`stream_find` — times out with no free slot; a `PullEngine` retrieve raises it (as `RetrieveBusyError`) when move-slot acquisition or same-key coalescing exceeds `move_lease_timeout` |
| `MoveToSelfError` | a C-MOVE completes reporting zero sub-operations — the query matched nothing (under-delivery/misrouting is `AssociationError`) |
| `ArrivalTimeoutError` | no instance arrives within the configured `arrival_timeout` |
| `NoPresentationContextError` | `StoreSession.store` found no accepted presentation context for the dataset — the association stays usable; nothing was sent |
