"""SQLite instance-level cache index (study/series/instance rows)."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    sop_uid       TEXT PRIMARY KEY,
    study_uid     TEXT NOT NULL,
    series_uid    TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    size          INTEGER NOT NULL,
    cached_at     REAL NOT NULL,
    last_accessed REAL NOT NULL,
    source        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_series ON instances(study_uid, series_uid);
CREATE INDEX IF NOT EXISTS idx_cached_at ON instances(cached_at);
CREATE INDEX IF NOT EXISTS idx_last_accessed ON instances(last_accessed);
"""


@dataclass(frozen=True)
class IndexedInstance:
    study_uid: str
    series_uid: str
    sop_uid: str
    file_path: str
    size: int
    cached_at: float
    last_accessed: float
    source: str


def _row(r: sqlite3.Row) -> IndexedInstance:
    return IndexedInstance(
        study_uid=r["study_uid"],
        series_uid=r["series_uid"],
        sop_uid=r["sop_uid"],
        file_path=r["file_path"],
        size=r["size"],
        cached_at=r["cached_at"],
        last_accessed=r["last_accessed"],
        source=r["source"],
    )


class CacheIndex:
    """Thread-safe SQLite index over cached DICOM instances."""

    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def upsert(self, inst: IndexedInstance) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO instances
                  (sop_uid, study_uid, series_uid, file_path,
                   size, cached_at, last_accessed, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sop_uid) DO UPDATE SET
                  study_uid=excluded.study_uid,
                  series_uid=excluded.series_uid,
                  file_path=excluded.file_path,
                  size=excluded.size,
                  cached_at=excluded.cached_at,
                  last_accessed=excluded.last_accessed,
                  source=excluded.source
                """,
                (
                    inst.sop_uid,
                    inst.study_uid,
                    inst.series_uid,
                    inst.file_path,
                    inst.size,
                    inst.cached_at,
                    inst.last_accessed,
                    inst.source,
                ),
            )
            self._conn.commit()

    def get_instance(self, sop_uid: str) -> IndexedInstance | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM instances WHERE sop_uid = ?", (sop_uid,))
            row = cur.fetchone()
        return _row(row) if row is not None else None

    def get_series(self, study_uid: str, series_uid: str) -> list[IndexedInstance]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM instances WHERE study_uid = ? AND series_uid = ?",
                (study_uid, series_uid),
            )
            rows = cur.fetchall()
        return [_row(r) for r in rows]

    def series_cached(self, study_uid: str, series_uid: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM instances WHERE study_uid = ? AND series_uid = ? LIMIT 1",
                (study_uid, series_uid),
            )
            return cur.fetchone() is not None

    def touch(self, sop_uid: str, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "UPDATE instances SET last_accessed = ? WHERE sop_uid = ?", (ts, sop_uid)
            )
            self._conn.commit()

    def total_size(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COALESCE(SUM(size), 0) AS total FROM instances")
            return int(cur.fetchone()["total"])

    def expired(self, ttl_seconds: float, now: float | None = None) -> list[IndexedInstance]:
        ts = time.time() if now is None else now
        cutoff = ts - ttl_seconds
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM instances WHERE cached_at < ?", (cutoff,)
            )
            rows = cur.fetchall()
        return [_row(r) for r in rows]

    def lru_over_size(self, max_size_bytes: int) -> list[IndexedInstance]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM instances ORDER BY last_accessed ASC"
            )
            rows = [_row(r) for r in cur.fetchall()]
        total = sum(r.size for r in rows)
        victims: list[IndexedInstance] = []
        for r in rows:
            if total <= max_size_bytes:
                break
            victims.append(r)
            total -= r.size
        return victims

    def delete(self, sop_uid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM instances WHERE sop_uid = ?", (sop_uid,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
