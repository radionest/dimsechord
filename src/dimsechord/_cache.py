"""Two-tier cache (memory + disk) backed by a SQLite instance index.

Synchronous and thread-safe: background disk writes run on a thread pool, so the
cache is usable identically from the asyncio HTTP face and from the synchronous
DIMSE C-MOVE generator (a pynetdicom worker thread).
The disk tier serves a series only when a completeness marker recorded at
fetch time matches the indexed rows; partial tee leftovers are never served.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pydicom
from cachetools import TTLCache
from pydicom import Dataset

from dimsechord._index import CacheIndex, IndexedInstance

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryCachedSeries:
    """In-memory cached series with O(1) instance lookup."""

    study_uid: str
    series_uid: str
    instances: dict[str, Any] = field(repr=False)
    cached_at: float
    disk_persisted: bool = False


_INSTANCE_OVERHEAD_BYTES = 16 * 1024
_PIXEL_KEYWORDS = ("PixelData", "FloatPixelData", "DoubleFloatPixelData")


def _series_size_bytes(entry: MemoryCachedSeries) -> int:
    """Estimated RAM footprint: pixel payloads + fixed per-instance overhead."""
    total = 0
    for ds in entry.instances.values():
        for keyword in _PIXEL_KEYWORDS:
            value = getattr(ds, keyword, None)
            if value is not None:
                total += len(value)
        total += _INSTANCE_OVERHEAD_BYTES
    return total


class DicomCache:
    """Two-tier cache: byte-bounded in-memory TTLCache + disk, with a SQLite index for disk."""

    def __init__(
        self,
        base_dir: Path | str,
        *,
        index_path: Path | str | None = None,
        ttl_hours: int = 24,
        max_size_gb: float = 10.0,
        memory_ttl_minutes: int = 30,
        memory_max_size_gb: float = 1.0,
        disk_write_concurrency: int = 4,
    ) -> None:
        if memory_max_size_gb <= 0:
            raise ValueError(f"memory_max_size_gb must be positive, got {memory_max_size_gb}")
        self._base_dir = Path(base_dir)
        db_path = Path(index_path) if index_path is not None else self._base_dir / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._index = CacheIndex(db_path)
        self._ttl_seconds = ttl_hours * 3600
        self._max_size_bytes = int(max_size_gb * 1024**3)
        self._memory_max_bytes = int(memory_max_size_gb * 1024**3)
        self._memory_cache: TTLCache[str, MemoryCachedSeries] = TTLCache(
            maxsize=self._memory_max_bytes,
            ttl=memory_ttl_minutes * 60,
            getsizeof=_series_size_bytes,
        )
        self._memory_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=disk_write_concurrency, thread_name_prefix="dimsechord-tee"
        )
        self._pending: set[Future[None]] = set()
        self._pending_lock = threading.Lock()

    def _key(self, study_uid: str, series_uid: str) -> str:
        return f"{study_uid}/{series_uid}"

    def _series_dir(self, study_uid: str, series_uid: str) -> Path:
        return self._base_dir / study_uid / series_uid

    @staticmethod
    def _validate_series_in_study(
        study_uid: str, series_uid: str, instances: dict[str, Any]
    ) -> None:
        """Raise if any instance's StudyInstanceUID differs from study_uid."""
        for sop_uid, ds in instances.items():
            actual = getattr(ds, "StudyInstanceUID", None)
            if not isinstance(actual, str):
                continue
            if actual != study_uid:
                raise RuntimeError(
                    f"Instance {sop_uid} of series {series_uid} does not belong to "
                    f"study {study_uid}: StudyInstanceUID is {actual}."
                )

    # ── memory tier ──────────────────────────────────────────────
    def get_series_from_memory(self, study_uid: str, series_uid: str) -> MemoryCachedSeries | None:
        with self._memory_lock:
            return self._memory_cache.get(self._key(study_uid, series_uid))

    def put_series_to_memory(
        self,
        study_uid: str,
        series_uid: str,
        instances: dict[str, Any],
        *,
        disk_persisted: bool = False,
    ) -> MemoryCachedSeries:
        self._validate_series_in_study(study_uid, series_uid, instances)
        entry = MemoryCachedSeries(
            study_uid=study_uid,
            series_uid=series_uid,
            instances=instances,
            cached_at=time.time(),
            disk_persisted=disk_persisted,
        )
        key = self._key(study_uid, series_uid)
        size = _series_size_bytes(entry)
        with self._memory_lock:
            if size > self._memory_max_bytes:
                # Larger than the whole budget: drop any stale entry under this key
                # rather than leave it silently servable from a prior, smaller put.
                self._memory_cache.pop(key, None)
                logger.warning(
                    f"Series {study_uid}/{series_uid} (~{size} bytes) "
                    f"exceeds the memory tier budget of {self._memory_max_bytes} bytes — "
                    f"serving without memory caching"
                )
            else:
                self._memory_cache[key] = entry
        return entry

    # ── disk tier (index-backed) ─────────────────────────────────
    def load_series_from_disk(self, study_uid: str, series_uid: str) -> dict[str, Dataset] | None:
        """Load a series from the disk tier, or None unless it is verifiably complete.

        Requires the completeness marker, an exact instance-row count match, and
        every file readable — a partial series is never returned (issue #15).
        LRU is refreshed once, after every row's file has read successfully; a
        failed load no longer touches any row, so it no longer refreshes LRU.
        """
        expected = self._index.series_expected_count(study_uid, series_uid)
        if expected is None:
            return None
        rows = self._index.get_series(study_uid, series_uid)
        if len(rows) != expected:
            logger.info(
                f"Disk series {study_uid}/{series_uid} incomplete: "
                f"{len(rows)} rows vs {expected} expected — ignoring"
            )
            return None
        instances: dict[str, Dataset] = {}
        for row in rows:
            try:
                ds = pydicom.dcmread(row.file_path)
            except Exception as e:
                logger.info(
                    f"Disk series {study_uid}/{series_uid} has unreadable file "
                    f"{row.file_path}: {e} — ignoring"
                )
                return None
            instances[str(ds.SOPInstanceUID)] = ds
        self._index.touch_many([row.sop_uid for row in rows])
        return instances

    def read_instance(self, study_uid: str, series_uid: str, sop_uid: str) -> Dataset | None:  # noqa: ARG002
        row = self._index.get_instance(sop_uid)
        if row is None:
            return None
        try:
            ds = pydicom.dcmread(row.file_path)
        except Exception as e:
            logger.warning(f"Failed to read instance {row.file_path}: {e}")
            return None
        self._index.touch(sop_uid)
        return ds

    def series_cached(self, study_uid: str, series_uid: str) -> bool:
        """Whether the disk tier holds the complete series (no file I/O performed)."""
        return self._index.series_cached(study_uid, series_uid)

    def mark_series_complete(self, study_uid: str, series_uid: str, expected_count: int) -> None:
        """Record that the disk tier holds the full series (expected_count instances)."""
        self._index.mark_series_complete(study_uid, series_uid, expected_count)

    # ── tee (synchronous + thread-pool background) ───────────────
    def write_instance(
        self, study_uid: str, series_uid: str, sop_uid: str, ds: Dataset, source: str = "pacs"
    ) -> None:
        """Write one instance to disk and commit its index row (blocking).

        Index commit is last: a failed write leaves no row → next request re-pulls.
        """
        series_dir = self._series_dir(study_uid, series_uid)
        series_dir.mkdir(parents=True, exist_ok=True)
        path = series_dir / f"{sop_uid}.dcm"
        ds.save_as(path, enforce_file_format=True)
        now = time.time()
        self._index.upsert(
            IndexedInstance(
                study_uid=study_uid,
                series_uid=series_uid,
                sop_uid=sop_uid,
                file_path=str(path),
                size=path.stat().st_size,
                cached_at=now,
                last_accessed=now,
                source=source,
            )
        )

    def _tee_one(
        self, study_uid: str, series_uid: str, sop_uid: str, ds: Dataset, source: str
    ) -> None:
        try:
            self.write_instance(study_uid, series_uid, sop_uid, ds, source)
        except Exception as e:
            logger.error(f"Tee write failed for {sop_uid}: {e}")

    def schedule_tee(
        self, study_uid: str, series_uid: str, sop_uid: str, ds: Dataset, source: str = "pacs"
    ) -> Future[None]:
        fut = self._executor.submit(self._tee_one, study_uid, series_uid, sop_uid, ds, source)
        with self._pending_lock:
            self._pending.add(fut)
        fut.add_done_callback(self._discard_future)
        return fut

    def _discard_future(self, fut: Future[None]) -> None:
        with self._pending_lock:
            self._pending.discard(fut)

    def flush_pending_writes(self, timeout: float = 30.0) -> None:
        with self._pending_lock:
            pending = list(self._pending)
        if pending:
            futures_wait(pending, timeout=timeout)

    def build_series_zip(self, cached: MemoryCachedSeries, output: IO[bytes]) -> int:
        count = 0
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for sop_uid, ds in cached.instances.items():
                buf = io.BytesIO()
                pydicom.dcmwrite(buf, ds, enforce_file_format=True)
                zf.writestr(f"{sop_uid}.dcm", buf.getvalue())
                count += 1
        return count

    # ── eviction (index-driven) ──────────────────────────────────
    def _remove_rows(self, rows: list[IndexedInstance]) -> int:
        study_dirs: set[Path] = set()
        touched_series: set[tuple[str, str]] = set()
        for row in rows:
            Path(row.file_path).unlink(missing_ok=True)
            study_dirs.add(self._series_dir(row.study_uid, row.series_uid))
            touched_series.add((row.study_uid, row.series_uid))
        self._index.delete_many([row.sop_uid for row in rows])
        self._index.clear_series_complete_many(sorted(touched_series))
        self._cleanup_empty_dirs(study_dirs)
        return len(rows)

    def _cleanup_empty_dirs(self, series_dirs: set[Path]) -> None:
        """rmdir empty study/series dirs, confined to the cache tree.

        Never removes ``base_dir`` itself or anything outside it — a guard
        against a caller-supplied study/series dir that resolves elsewhere.
        """
        for series_dir in series_dirs:
            for d in (series_dir, series_dir.parent):
                if (
                    d.is_relative_to(self._base_dir)
                    and d != self._base_dir
                    and d.exists()
                    and d.is_dir()
                    and not any(d.iterdir())
                ):
                    d.rmdir()

    def evict_expired(self) -> int:
        removed = self._remove_rows(self._index.expired(self._ttl_seconds))
        if removed:
            logger.info(f"Evicted {removed} expired cache instances")
        return removed

    def evict_orphans(self, min_age_seconds: float = 3600.0) -> int:
        """Remove .dcm files that have no index row and are older than the guard.

        A crash between the tee's file write and its index upsert (index commit
        is last, see ``write_instance``) leaves a file no index-driven eviction
        can ever see. The age guard keeps in-flight tees safe: their
        file-before-row window is milliseconds, not hours. A candidate is
        checked against the index by exact file path, not just SOP UID, so a
        re-homed instance's old file is still swept; each is also re-stat'd
        immediately before removal so a concurrent tee re-writing the same SOP
        during the sweep is spared.
        """
        cutoff = time.time() - min_age_seconds
        candidates: list[Path] = []
        for path in self._base_dir.rglob("*.dcm"):
            try:
                if path.stat().st_mtime < cutoff:
                    candidates.append(path)
            except OSError:
                continue  # raced a concurrent eviction; skip
        if not candidates:
            return 0
        indexed_paths = self._index.existing_file_paths([p.stem for p in candidates])
        removed = 0
        dirs: set[Path] = set()
        for path in candidates:
            if str(path) in indexed_paths:
                continue
            try:
                # A concurrent tee re-writing this same SOP since collection
                # refreshes its mtime — spare it rather than race the write.
                if path.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue  # gone since collection — nothing to unlink
            path.unlink(missing_ok=True)
            removed += 1
            dirs.add(path.parent)
        self._cleanup_empty_dirs(dirs)
        if removed:
            logger.info(f"Evicted {removed} orphan cache files")
        return removed

    def evict_by_size(self) -> int:
        removed = self.evict_orphans()
        removed += self._remove_rows(self._index.lru_over_size(self._max_size_bytes))
        if removed:
            logger.info(f"Evicted {removed} cache instances by size")
        return removed

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
        with self._memory_lock:
            self._memory_cache.clear()
        self._index.close()
        logger.info("DicomCache shutdown complete")
