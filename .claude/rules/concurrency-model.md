---
paths:
  - "src/dimsechord/_pool.py"
  - "src/dimsechord/_scu.py"
  - "src/dimsechord/_scp.py"
  - "src/dimsechord/_pull_engine.py"
  - "src/dimsechord/_cache.py"
  - "src/dimsechord/_handlers.py"
  - "src/dimsechord/_bridge.py"
  - "src/dimsechord/_query_engine.py"
  - "src/dimsechord/_store_session.py"
---

# Concurrency model: sync core, thin async adapters

`pynetdicom` is synchronous and thread-based — C-STORE/C-MOVE event
handlers run in pynetdicom's own worker threads, which cannot `await`.
dimsechord's core is therefore synchronous and thread-safe, with thin
`async` adapters layered on top for the HTTP face.

## The pattern

- **Sync core**: `_scu.py` (`DicomOperations`, including the raw
  `find_iter` generator), `_scp.py` (`StorageSCP`'s receive queue),
  `_pull_engine.py`'s synchronous generator, `_query_engine.py`'s
  `iter_find`, `_cache.py`'s read/write paths, `_store_session.py`
  (`StoreSession`) — all safe to call from a pynetdicom worker thread;
  `StoreSession` alone is single-owner (no internal locking), so one
  session is used by one thread at a time.
- **Thin async adapter**: `DicomClient`, `PullEngine`'s async methods and
  `QueryEngine.stream_find` run the sync path in a worker thread and
  bridge each item back to the asyncio event loop — the streaming faces
  through the shared bounded `_bridge.iter_to_aiter` — they do not
  reimplement the underlying logic.

New sync/async coupling should follow this pattern: put the logic in the
sync core once, adapt it for async, never maintain two parallel
implementations.

## Specific mechanisms

- `AssociationPool` (`_pool.py`, decision "D6") — leases one of N AE
  Title identities at a time, with independent per-AET caps for move
  (`per_aet_cap`) and find (`per_aet_find_cap`) leases; `lease` /
  `lease_find` block or raise `PoolExhaustedError` when every slot for
  an identity is busy. `PullEngine` always supplies a bounded timeout
  (`move_lease_timeout`, default 5 s) for its move-slot lease and its
  same-key coalescing wait, so a retrieve fails fast instead of queuing;
  the coalescing wait raises `RetrieveBusyError`, a `PoolExhaustedError`
  subclass. A move-slot lease is released only after the driver thread has
  exited — via a reaper thread when it outlives the 2 s abort join (a
  cross-thread `Association.abort()` cannot wake a driver parked in the
  DIMSE receive; that wait only resolves at `dimse_timeout`, default 30 s,
  per pynetdicom 3.0.4) — never while it may still be alive, so a re-leased
  AET can never receive a dying move's stray C-STOREs into a fresh session;
  if the reaper itself fails to start, the slot is deliberately leaked (with
  an error log) rather than released. With N > 1 pooled AETs the guarantee
  is per-AET only: a same-key retry through a different AET can still catch
  a dying move's last in-flight C-STORE — certification stays safe, worst
  case one duplicated instance mid-stream.
- `DicomCache` (`_cache.py`) — two-tier (memory + disk) backed by a SQLite
  index; background disk writes run on a `ThreadPoolExecutor` so the same
  cache instance is safe to use from both the asyncio HTTP face and the
  synchronous DIMSE C-MOVE generator. The memory tier is byte-budgeted
  (`memory_max_size_gb`, enforced through a `getsizeof` size estimate) and
  guarded by an internal lock — cachetools caches are not thread-safe on
  their own. The disk tier serves a series only when a `series_complete`
  marker (written at clean transport-stream end) matches the indexed row
  count; any desync — pending tees, lost files, partial eviction, or an
  aborted pull — fails the read and re-pulls, so a truncated series is never
  served. `evict_orphans` (default `min_age_seconds=3600`) sweeps `.dcm`
  files left on disk with no matching index row — a crash between a tee's
  file write and its index commit — and runs automatically inside
  `evict_by_size`, so existing eviction timers pick it up.
- `StorageSCP` (`_scp.py`) — a persistent Storage SCP whose C-STORE
  handler pushes received instances onto a bounded per-session queue
  (`session_queue_maxsize`, default 64) that `PullEngine` streams from,
  applying backpressure to a PACS that outruns the consumer. Accepts up to
  `maximum_associations` concurrent associations (default 25, explicit —
  previously pynetdicom's implicit 10).
