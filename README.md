# dimsechord

Pure-Python toolkit of composable building blocks for DICOM and DICOMweb services.

`dimsechord` wraps [`pydicom`](https://github.com/pydicom/pydicom) and
[`pynetdicom`](https://github.com/pydicom/pynetdicom) behind a small, stable
public API. It gives you the networking, caching, and conversion primitives
needed to build PACS proxies, DICOMweb gateways, and imaging pipelines — and
nothing else: there is no bundled HTTP framework and no opinionated request
handlers, so you bring your own web layer.

## Features

- **DIMSE SCU** — C-FIND, C-STORE, C-MOVE and C-GET, exposed through an async
  `DicomClient`.
- **C-STORE SCP** — receive incoming instances with `StorageSCP`, one listener
  per distinct port; multiple AETs may share a port. All transfer syntaxes
  are accepted by default, so compressed objects arrive verbatim.
- **C-STORE SCU session** — `StoreSession` is a persistent, association-scoped
  C-STORE sender: the peer's DIMSE status passes through verbatim as an
  `int` (success, warning, and failure alike), and a presentation-context
  miss raises `NoPresentationContextError` without tearing down the
  association.
- **AssociationPool** — manage multiple AE-Title identities with per-AET
  concurrency limits.
- **Two-tier cache** — in-memory + disk, backed by a SQLite instance index.
- **Streaming pull-engine** — move-to-self retrieval streamed instance by
  instance, with per-UID request coalescing.
- **Streaming query-engine** — raw C-FIND pass-through via `QueryEngine`,
  streamed response by response under independent per-AET find leases.
- **DICOMweb conversion** — DICOM ↔ DICOMweb JSON and `multipart/related`
  frame responses.

Runtime dependencies are just `pydicom`, `pynetdicom`, and `cachetools`.

## Installation

```bash
pip install dimsechord
```

Requires Python 3.12+.

## Quickstart

```python
import asyncio

from dimsechord import (
    AssociationPool, DicomCache, DicomClient, DicomNode, PullEngine,
    SeriesQuery, StorageSCP, StudyQuery, convert_datasets_to_dicom_json,
)

PACS = DicomNode(aet="PACS", host="127.0.0.1", port=11112)


async def main() -> None:
    # 1. Find studies and series (C-FIND, QIDO-style).
    client = DicomClient(calling_aet="MYSCU")
    studies = await client.find_studies(StudyQuery(patient_id="12345"), PACS)
    series = await client.find_series(
        SeriesQuery(study_instance_uid=studies[0].study_instance_uid), PACS)

    # 2. Pull a series move-to-self and build DICOMweb JSON (WADO-style).
    pool = AssociationPool(aets=["MYDEST"])
    scp = StorageSCP()
    scp.start({"MYDEST": 11113})
    # Your PACS must route the AET "MYDEST" back to this SCP's host:port.
    cache = DicomCache(base_dir="./cache", index_path="./cache/index.db")
    engine = PullEngine(pool=pool, scp=scp, cache=cache, pacs=PACS)
    try:
        cached = await engine.ensure_series(
            studies[0].study_instance_uid, series[0].series_instance_uid)
        metadata = convert_datasets_to_dicom_json(
            list(cached.instances.values()), base_url="https://example.org/dicom-web")
        print(len(metadata), "instances")
    finally:
        scp.stop()
        cache.shutdown()


asyncio.run(main())
```

> **0.8.0:** every wait on the C-MOVE-to-self retrieve path is now short and
> bounded, failing with a typed error instead of queuing for minutes — the
> new `RetrieveBusyError` (a `PoolExhaustedError` subclass) covers same-key
> coalescing. An abandoned move is aborted upstream immediately; its slot
> returns once the driver thread exits.

> **0.7.0:** `StoreSession` is a persistent C-STORE SCU session with verbatim
> DIMSE status pass-through — success, warning, and failure codes all return
> as an `int`, never raised. A presentation-context miss raises the new
> `NoPresentationContextError` instead; `build_storage_scp_contexts()` is the
> new acceptor-side mirror of `build_storage_scu_contexts()`.

> **0.6.0 (breaking):** `DicomCache`'s memory tier is now sized by bytes, not
> entry count — `memory_max_entries` (default `50`) is replaced by
> `memory_max_size_gb` (default `1.0`).

> **0.5.1:** `StorageSCP` accepts all transfer syntaxes by default and the new
> `build_storage_scu_contexts()` builds forwarding-SCU contexts, so compressed
> objects pass through verbatim. C-GET now negotiates a curated storage-class
> set (see CHANGELOG).

> **0.5.0:** the disk cache now records per-series completeness; series cached
> by earlier versions re-pull once on first read after upgrade (the SQLite
> schema migrates automatically).

> **0.4.0 (breaking):** `StorageSCP.start` now takes an `{AET: port}` mapping
> (was `start(aets, port)`), binding one listener per port.

## Public API

The supported surface is exactly what `dimsechord/__init__.py` exports
(`from dimsechord import …`); it is frozen by `tests/unit/test_public_api.py`.

Everything else lives in underscore-prefixed modules (`dimsechord._scu`,
`dimsechord._cache`, …) and is **private** — importing from those modules is
unsupported and may break without notice. If you need something that is not
exported, extend the public surface rather than reaching into a private module.

## Documentation

- [Why dimsechord](docs/why.md) — what problems it solves.
- [Typing](docs/typing.md) — how the typed API works with mypy/pyright.
- [Cookbook](docs/cookbook.md) — one recipe per feature.
- [Gateway tutorial](docs/tutorial-gateway.md) — an end-to-end DICOMweb gateway.

## License

MIT — see [LICENSE](https://github.com/radionest/dimsechord/blob/main/LICENSE).
