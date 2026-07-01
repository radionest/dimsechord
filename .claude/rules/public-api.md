---
paths: "src/dimsechord/**"
---

# Public API convention

`src/dimsechord/__init__.py` is the **only** supported public surface.
Every other module is private:

- A leading underscore in the filename (`_scu.py`, `_client.py`,
  `_handlers.py`, `_models.py`, ...) means importing it directly is
  unsupported and may break without notice — several of these modules say
  so explicitly, as a top-of-file comment in `_scu.py`/`_handlers.py`, or
  in the module docstring in `_models.py`.
- The public surface centers on two orchestration entry points —
  `DicomClient` (async C-FIND / C-STORE / C-MOVE / C-GET) and
  `PullEngine` (move-to-self / C-GET retrieval) — plus
  directly-constructed building blocks (`StorageSCP`, `AssociationPool`,
  `DicomCache`) and standalone converter/multipart utilities, all
  re-exported from `__init__.py`. If a use case genuinely isn't covered
  by any of them, **extend** `__init__.py`'s exports — add a
  method/function to a private module and re-export it — rather than
  importing the private module directly.

## Do not import directly

- `DicomOperations` (`_scu.py`) — reach it through `DicomClient` instead.
- `StorageHandler` / `create_store_handler` (`_handlers.py`) — reach it
  through `StorageSCP` + `PullEngine` instead.
- `AssociationConfig`, `RetrieveRequest`, `StorageConfig`, `StorageMode`
  (`_models.py`) — internal-only dataclasses that drive the private
  SCU/handlers, not part of the public model set (`StudyQuery` /
  `SeriesQuery` / `ImageQuery` / `*Result` / `DicomNode` /
  `QueryRetrieveLevel` are public and re-exported from `__init__.py`).

Source of truth: the `__init__.py` module docstring. If it and this file
ever disagree, `__init__.py` wins — update this file, don't restate the
docstring here.
