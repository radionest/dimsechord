# Changelog

## 0.7.0 — Unreleased

The typed Q/R face now speaks the Study Root information model exclusively
(issue #22): every identifier is hierarchically complete by construction. A
new `StoreSession` adds a persistent C-STORE SCU write path with verbatim
DIMSE status pass-through.

### Added

- `StoreSession` — a persistent, association-scoped C-STORE SCU: lazy open,
  verbatim `int` DIMSE status pass-through (success, warnings, and failures
  are all return values, never exceptions), typed `NoPresentationContextError`
  on a context miss, duplicate-safe reconnect-on-drop, and idempotent
  `close()` plus context-manager support. Deliberately bypasses
  `DicomClient.set_max_concurrent_associations` (callers govern session
  concurrency).
- `NoPresentationContextError` — a `DimsechordError` (not an
  `AssociationError`) carrying `sop_class_uid`/`transfer_syntax`, raised
  before sending when no accepted presentation context matches; the
  association stays open and usable.
- `build_storage_scp_contexts()` — the acceptor-side mirror of
  `build_storage_scu_contexts()`, built from the same storage-class ×
  transfer-syntax matrix: every instance a `StoreSession` can propose
  upstream is accepted by an SCP built from it.

### Changed

- **Breaking:** all typed C-FIND/C-MOVE/C-GET operations use the Study Root
  information model. Previously they used Patient Root with an incomplete
  key hierarchy (no `PatientID` above the STUDY level), which at least one
  field SCP answered by silently widening a series query to the whole
  study. Patient-Root-only peers now fail with a clear `AssociationError`;
  for queries, the raw pass-through face (`QueryEngine` / `find_iter`)
  remains their supported path — there is no raw retrieve face, so
  C-MOVE/C-GET against such peers is no longer possible.
- **Breaking:** the `patient_id` parameter is removed from
  `DicomClient.move_study`, `move_series`, `get_study`, `get_series`,
  `get_study_to_memory`, and `get_series_to_memory` — a Study Root
  retrieve identifier carries only the Q/R level and unique keys. The
  optional parameters of these methods (`timeout`, `on_progress`) are now
  keyword-only, so a legacy positional `patient_id` argument fails with
  `TypeError` instead of silently binding to the parameter that took its
  position.
- **Breaking:** `QueryRetrieveLevel.PATIENT` is removed; the enum now
  lists exactly the levels the library operates at
  (`STUDY`/`SERIES`/`IMAGE`).
- A peer that refuses a needed Study Root presentation context raises
  `AssociationError` naming the model instead of a bare pynetdicom
  `ValueError`; an association where the peer accepts none of the
  requested contexts reports which contexts were rejected.

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
