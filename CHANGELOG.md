# Changelog

## 0.6.0 — 2026-07-13

`DicomCache`'s memory tier is now sized by bytes, not entry count.

### Changed

- **Breaking:** the `memory_max_entries` (default `50`) constructor
  parameter is replaced by `memory_max_size_gb` (default `1.0`). Eviction
  now reflects actual per-series memory footprint (pixel payloads plus a
  fixed per-instance overhead) instead of a fixed item count.

### Fixed

- `put_series_to_memory` no longer leaves a stale, previously-cached entry
  servable when a later put for the same key exceeds the memory budget.
- `DicomCache` now rejects a non-positive `memory_max_size_gb` at
  construction instead of silently treating every series as oversized.

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
