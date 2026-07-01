---
paths:
  - "src/dimsechord/_pool.py"
  - "src/dimsechord/_scu.py"
  - "src/dimsechord/_scp.py"
  - "src/dimsechord/_pull_engine.py"
  - "src/dimsechord/_cache.py"
  - "src/dimsechord/_handlers.py"
---

# Concurrency model: sync core, thin async adapters

`pynetdicom` is synchronous and thread-based — C-STORE/C-MOVE event
handlers run in pynetdicom's own worker threads, which cannot `await`.
dimsechord's core is therefore synchronous and thread-safe, with thin
`async` adapters layered on top for the HTTP face.

## The pattern

- **Sync core**: `_scu.py` (`DicomOperations`), `_scp.py` (`StorageSCP`'s
  receive queue), `_pull_engine.py`'s synchronous generator, `_cache.py`'s
  read/write paths — all safe to call from a pynetdicom worker thread.
- **Thin async adapter**: `DicomClient` and `PullEngine`'s async methods
  run the sync iterator in a thread-pool worker and bridge each item back
  to the asyncio event loop — they do not reimplement the underlying
  logic.

New sync/async coupling should follow this pattern: put the logic in the
sync core once, adapt it for async, never maintain two parallel
implementations.

## Specific mechanisms

- `AssociationPool` (`_pool.py`, decision "D6") — leases one of N AE
  Title identities at a time, with a per-AET concurrency cap; blocks or
  raises `PoolExhaustedError` when every slot for an identity is busy.
- `DicomCache` (`_cache.py`) — two-tier (memory + disk) backed by a
  SQLite index; background disk writes run on a `ThreadPoolExecutor` so
  the same cache instance is safe to use from both the asyncio HTTP face
  and the synchronous DIMSE C-MOVE generator.
- `StorageSCP` (`_scp.py`) — a persistent Storage SCP whose C-STORE
  handler pushes received instances onto a queue that `PullEngine`
  streams from.
