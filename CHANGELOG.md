# Changelog

## 0.5.1 — 2026-07-06

Compressed transfer syntaxes now pass through verbatim end to end.

### Fixed

- `StorageSCP` accepts all transfer syntaxes by default (configurable via the
  new `supported_transfer_syntaxes` constructor parameter). Previously only
  uncompressed/Deflated syntaxes were accepted, so a C-MOVE of a compressed
  series failed every sub-operation or forced the PACS to transcode.
- `DicomClient` C-STORE (`store_instance` / `store_instances_batch`) derives
  its presentation contexts from the datasets being sent, so compressed
  datasets now find an exactly matching context.

### Added

- `build_storage_scu_contexts()` — public builder for a forwarding SCU's
  requested contexts: per image class, one uncompressed context plus one
  context per compressed transfer syntax; per other class, one uncompressed
  context. Defaults produce 106 contexts (limit: 128).
- `DEFAULT_COMPRESSED_TRANSFER_SYNTAXES`, `DEFAULT_IMAGE_STORAGE_CLASSES`,
  `DEFAULT_OTHER_STORAGE_CLASSES` — the builder's configurable defaults.

### Changed

- C-GET now negotiates the curated storage contexts from
  `build_storage_scu_contexts()` (26 SOP classes, compressed syntaxes
  included) instead of 126 arbitrary storage classes with uncompressed
  syntaxes only. Retrieval of SOP classes outside the curated set via C-GET
  is no longer negotiated; C-MOVE-to-self is unaffected.

## 0.5.0 and earlier

See the git history.
