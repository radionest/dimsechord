# Tutorial: a DICOMweb gateway

This tutorial wires dimsechord's blocks into a small
[DICOMweb](https://www.dicomstandard.org/using/dicomweb) gateway: QIDO-RS study
and series lists, plus WADO-RS metadata and frames. It uses
[Starlette](https://www.starlette.io/) for the HTTP layer.

> **Starlette is illustrative, not a dependency.** dimsechord has no web layer;
> the same `async` methods drop into FastAPI, aiohttp, or any ASGI app. Install
> the tutorial's extras with `pip install starlette uvicorn`.

## Lifecycle

The gateway owns one `DicomClient` for queries and one `PullEngine` (plus its
pool, SCP, and cache) for retrieval. Build them on startup and tear them down on
shutdown:

```python
from contextlib import asynccontextmanager

from dimsechord import (
    AssociationPool, DicomCache, DicomClient, DicomNode, PullEngine, StorageSCP,
)

PACS = DicomNode(aet="PACS", host="127.0.0.1", port=11112)


class Gateway:
    def __init__(self) -> None:
        self.client = DicomClient(calling_aet="GWSCU")
        self.pool = AssociationPool(aets=["GWDEST"])
        self.scp = StorageSCP()
        self.cache = DicomCache(
            base_dir="/var/cache/dimsechord",
            index_path="/var/cache/dimsechord/index.db",
        )
        self.engine: PullEngine | None = None

    def start(self) -> None:
        self.scp.start(aets=self.pool.aets, port=11113)
        self.engine = PullEngine(
            pool=self.pool, scp=self.scp, cache=self.cache, pacs=PACS)

    def stop(self) -> None:
        self.scp.stop()
        self.cache.shutdown()


gw = Gateway()


@asynccontextmanager
async def lifespan(app):
    gw.start()
    yield
    gw.stop()
```

## QIDO-RS: study and series lists

Run a C-FIND and convert each result to DICOM-JSON:

```python
from starlette.requests import Request
from starlette.responses import JSONResponse

from dimsechord import (
    SeriesQuery, StudyQuery, series_result_to_dicom_json, study_result_to_dicom_json,
)


async def qido_studies(request: Request) -> JSONResponse:
    studies = await gw.client.find_studies(StudyQuery(), PACS)
    return JSONResponse([study_result_to_dicom_json(s) for s in studies])


async def qido_series(request: Request) -> JSONResponse:
    study = request.path_params["study"]
    series = await gw.client.find_series(SeriesQuery(study_instance_uid=study), PACS)
    return JSONResponse([series_result_to_dicom_json(s) for s in series])
```

## WADO-RS: series metadata

Pull the series move-to-self (streamed and cached), then convert the datasets to
DICOM-JSON. DICOM-JSON is a single array, so this gathers the streamed instances
before responding; switch to incremental output only if you need true streaming:

```python
from dimsechord import convert_datasets_to_dicom_json


async def wado_metadata(request: Request) -> JSONResponse:
    assert gw.engine is not None
    study = request.path_params["study"]
    series = request.path_params["series"]
    instances = [ds async for ds in gw.engine.stream_series(study, series)]
    base = str(request.base_url).rstrip("/") + "/dicom-web"
    return JSONResponse(convert_datasets_to_dicom_json(instances, base_url=base))
```

## WADO-RS: frames

Frames are raw bytes, so this endpoint serves a `multipart/related` body
directly:

```python
from starlette.responses import Response

from dimsechord import build_multipart_response, extract_frames_from_dataset


async def wado_frames(request: Request) -> Response:
    assert gw.engine is not None
    study = request.path_params["study"]
    series = request.path_params["series"]
    sop = request.path_params["sop"]
    nums = [int(n) for n in request.path_params["frames"].split(",")]
    cached = await gw.engine.ensure_series(study, series)
    body, content_type = build_multipart_response(
        extract_frames_from_dataset(cached.instances[sop], nums))
    return Response(body, media_type=content_type)
```

## Assemble the app

```python
from starlette.applications import Starlette
from starlette.routing import Route

app = Starlette(lifespan=lifespan, routes=[
    Route("/studies", qido_studies),
    Route("/studies/{study}/series", qido_series),
    Route("/studies/{study}/series/{series}/metadata", wado_metadata),
    Route("/studies/{study}/series/{series}/instances/{sop}/frames/{frames}",
          wado_frames),
])
# Run with:  uvicorn gateway:app
```

## Notes

- The PACS must route the `GWDEST` AET back to the SCP's host and port — see
  [why move-to-self is hard](why.md#move-to-self).
- For C-GET (no SCP, no routing), build the engine with
  `PullEngine.via_cget(...)` — see [the pull recipe](cookbook.md#pull).
- For failure modes (`MoveToSelfError`, `ArrivalTimeoutError`, …) see
  [error handling](cookbook.md#errors).
