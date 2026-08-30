# Changelog

## 0.8.0 — UNRELEASED

Every wait on the C-MOVE-to-self retrieve path is now short and bounded: no
free move slot, or a same-series fetch already in flight, fails fast instead
of queuing for minutes. Each wait — same-key coalescing, then move-slot
acquisition — is bounded by `move_lease_timeout` (default 5 s) and raises a
typed error; the two are sequential, so a retrieve crosses at most two such
waits, worst-case ~2x the knob. An abandoned move aborts the upstream
association instead of being joined for up to `cmove_timeout`. Motivated by a
production queue collapse where one slow upstream C-MOVE blocked clients for
8–11 minutes.

### Added

- `RetrieveBusyError` — raised when the same series/study is already being
  retrieved and no coalescing slot frees within `move_lease_timeout`.
  Subclasses `PoolExhaustedError`, so existing handlers that map pool
  exhaustion to a retry-later DIMSE status (0xA702) catch it unchanged.
- `PullEngine(move_lease_timeout=…)` bounds move-slot acquisition and the
  same-key coalescing wait; `PullEngine.via_cget(coalesce_timeout=…)` bounds
  coalescing only — a C-GET engine has no move to lease.
- `StorageSCP(maximum_associations=…)` (default 25, previously pynetdicom's
  implicit 10) and `StorageSCP(session_queue_maxsize=…)` (default 64) — the
  per-session streaming queue is now bounded, applying C-STORE backpressure
  to a PACS that outruns the consumer. If a live consumer stalls long enough
  for the bounded queue to park the C-STORE handler past the PACS's own
  DIMSE timeout, the PACS may abort and the retrieve fails with
  `AssociationError` (0.7.0's unbounded queue absorbed this lag); size
  `session_queue_maxsize` accordingly.
- `DicomCache.evict_orphans(min_age_seconds=3600)` — sweeps `.dcm` files left
  on disk without index rows (crash mid-tee); also runs inside
  `evict_by_size()` at most once per hour, so existing eviction timers pick
  it up.

### Changed

- **Breaking (behavior):** retrieves fail fast. The move slot is leased in
  `stream()` before any upstream work with `move_lease_timeout` (was: inside
  the driver thread with `timeout=cmove_timeout`, ~300 s), and the per-key
  coalescing locks no longer wait forever. Callers that relied on requests
  queuing behind a slow fetch now receive `PoolExhaustedError` /
  `RetrieveBusyError` immediately.
- An abandoned retrieve (client disconnect, HTTP cancel, arrival timeout)
  aborts the in-flight upstream C-MOVE association: the consumer unblocks
  within ~2 s, the upstream stops sending as soon as it notices the aborted
  association (typically its next sub-operation), and the move slot
  returns as soon as the driver thread exits — bounded by the DIMSE timeout
  (default 30 s) instead of the full move duration. The slot is never
  released while the driver thread lives, so a re-leased AET cannot receive
  a dying move's stray C-STOREs. `cmove_timeout` is retained for API
  compatibility but no longer bounds the consumer join; cancellation is
  abort-based.
- `MoveSession` buffers once: streaming sessions (PullEngine) deliver through
  the bounded queue and no longer duplicate every instance into
  `MoveSession.instances`; collect sessions (`retrieve_via_move`) retain
  instances and no longer enqueue.

### Fixed

- Eviction and series reads no longer commit per row: LRU victim selection
  streams from a SQL cursor instead of loading the whole table, deletes and
  `last_accessed` updates are chunk-batched (a 1931-instance series read was
  1931 commits; now 1 per 500).
- C-STOREs arriving for an abandoned move no longer pile into a dead
  session's unbounded queue ("unregistered session" storm): the abort stops
  the flow at the source and a full queue on an ended session drops.

## 0.7.0 — 2026-07-24

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
