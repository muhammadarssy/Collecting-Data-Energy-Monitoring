"""Outbox SQLite lokal untuk store-and-forward ke PostgreSQL.

Tahan crash proses (WAL). Bukan pengganti DB — hanya antrian FIFO
saat PostgreSQL mati / timeout.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


class LocalSpool:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_CREATE)
        self._conn.commit()
        self._depth = int(self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
        if self._depth:
            log.warning("spool_recovered", path=str(path), depth=self._depth)

    @property
    def depth(self) -> int:
        return self._depth

    def enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        blob = json.dumps(payload, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO outbox (kind, payload, created_at) VALUES (?, ?, ?)",
                (kind, blob, now),
            )
            self._conn.commit()
            self._depth += 1
            depth = self._depth
        if depth in (1, 10, 100) or depth % 1000 == 0:
            log.warning("spool_queued", kind=kind, depth=depth)

    def peek(self, limit: int) -> list[tuple[int, str, dict[str, Any]]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, payload FROM outbox ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(int(r[0]), str(r[1]), json.loads(r[2])) for r in rows]

    def delete(self, row_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            self._conn.commit()
            if cur.rowcount:
                self._depth = max(0, self._depth - cur.rowcount)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
