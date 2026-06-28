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
- **C-STORE SCP** — receive incoming instances with `StorageSCP`.
- **AssociationPool** — manage multiple AE-Title identities with per-AET
  concurrency limits.
- **Two-tier cache** — in-memory + disk, backed by a SQLite instance index.
- **Streaming pull-engine** — move-to-self retrieval streamed instance by
  instance, with per-UID request coalescing.
- **DICOMweb conversion** — DICOM ↔ DICOMweb JSON and `multipart/related`
  frame responses.

Runtime dependencies are just `pydicom`, `pynetdicom`, and `cachetools`.

## Installation

```bash
pip install dimsechord
```

Requires Python 3.12+.

## Public API

The supported surface is exactly what `dimsechord/__init__.py` exports
(`from dimsechord import …`); it is frozen by `tests/unit/test_public_api.py`.

Everything else lives in underscore-prefixed modules (`dimsechord._scu`,
`dimsechord._cache`, …) and is **private** — importing from those modules is
unsupported and may break without notice. If you need something that is not
exported, extend the public surface rather than reaching into a private module.

## License

MIT — see [LICENSE](https://github.com/radionest/dimsechord/blob/main/LICENSE).
