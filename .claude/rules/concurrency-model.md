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
  `iter_find`, `_cache.py`'s read/write paths — all safe to call from a
  pynetdicom worker thread.
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
  an identity is busy.
- `DicomCache` (`_cache.py`) — two-tier (memory + disk) backed by a
  SQLite index; background disk writes run on a `ThreadPoolExecutor` so
  the same cache instance is safe to use from both the asyncio HTTP face
  and the synchronous DIMSE C-MOVE generator.
- `StorageSCP` (`_scp.py`) — a persistent Storage SCP whose C-STORE
  handler pushes received instances onto a queue that `PullEngine`
  streams from.
