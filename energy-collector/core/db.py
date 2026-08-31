"""Lapisan PostgreSQL via asyncpg + outbox lokal.

- Insert per sample (real-time, bukan batch) saat sesi aktif.
- Update tabel tracking `production_sessions` & `production_cycles` saat transisi.
- Kalau PG mati/timeout: tulis ke SQLite spool, flush FIFO saat PG hidup lagi.
- Error tidak mematikan polling loop.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import asyncpg
import structlog

from core.spool import LocalSpool
from models.meter_reading import MEASUREMENT_FIELDS, MeterReading

log = structlog.get_logger(__name__)

_COMMAND_TIMEOUT = 10.0
_CONNECT_TIMEOUT = 10.0
_FLUSH_BATCH = 100

KIND_INSERT = "insert_reading"
KIND_START_SESSION = "start_session"
KIND_END_SESSION = "end_session"
KIND_OPEN_CYCLE = "open_cycle"
KIND_CLOSE_CYCLE = "close_cycle"


def _build_insert_sql() -> str:
    cols = ["time", "session_id", "cycle_id", "meter_id", "device_type", *MEASUREMENT_FIELDS]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(cols)
    return f"INSERT INTO meter_readings ({col_list}) VALUES ({placeholders})"


_INSERT_SQL = _build_insert_sql()

_SQL_START_SESSION = (
    "INSERT INTO production_sessions (session_id, meter_id, start_time) "
    "VALUES ($1, $2, $3) ON CONFLICT (session_id) DO NOTHING"
)
_SQL_END_SESSION = "UPDATE production_sessions SET end_time = $2 WHERE session_id = $1"
_SQL_OPEN_CYCLE = (
    "INSERT INTO production_cycles (cycle_id, session_id, meter_id, start_time) "
    "VALUES ($1, $2, $3, $4) ON CONFLICT (cycle_id) DO NOTHING"
)
_SQL_CLOSE_CYCLE = (
    "UPDATE production_cycles pc SET "
    "end_time = $2, "
    "impep = COALESCE(("
    "  SELECT MAX(r.impep) - MIN(r.impep) "
    "  FROM meter_readings r WHERE r.cycle_id = $1"
    "), 0), "
    "expep = COALESCE(("
    "  SELECT MAX(r.expep) - MIN(r.expep) "
    "  FROM meter_readings r WHERE r.cycle_id = $1"
    "), 0) "
    "WHERE pc.cycle_id = $1"
)


def _as_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if value is None:
        return None
    return uuid.UUID(value)


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _reading_payload(reading: MeterReading) -> dict[str, Any]:
    return {
        "meter_id": reading.meter_id,
        "timestamp": reading.timestamp.isoformat(),
        "session_id": reading.session_id,
        "cycle_id": reading.cycle_id,
        "device_type": reading.device_type,
        "values": reading.values,
    }


def _reading_from_payload(payload: dict[str, Any]) -> MeterReading:
    return MeterReading(
        meter_id=payload["meter_id"],
        timestamp=_dt(payload["timestamp"]),
        session_id=payload.get("session_id"),
        cycle_id=payload.get("cycle_id"),
        device_type=payload.get("device_type", "energy"),
        values=payload.get("values") or {},
    )


class Database:
    """Wrapper connection pool asyncpg + store-and-forward spool."""

    def __init__(
        self,
        dsn: str,
        spool_path: Path,
        min_size: int = 2,
        max_size: int = 10,
    ):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None
        self._connect_lock = asyncio.Lock()
        self.spool = LocalSpool(spool_path)

    @property
    def ready(self) -> bool:
        return self._pool is not None

    @property
    def spool_depth(self) -> int:
        return self.spool.depth

    async def connect(self) -> bool:
        return await self.ensure_connected()

    async def ensure_connected(self) -> bool:
        async with self._connect_lock:
            if self._pool is not None:
                try:
                    async with self._pool.acquire() as conn:
                        await conn.execute("SELECT 1")
                    return True
                except Exception as exc:
                    log.warning("db_pool_unhealthy", error=str(exc))
                    old = self._pool
                    self._pool = None
                    try:
                        await old.close()
                    except Exception:
                        pass
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self.dsn,
                    min_size=self.min_size,
                    max_size=self.max_size,
                    command_timeout=_COMMAND_TIMEOUT,
                    timeout=_CONNECT_TIMEOUT,
                )
                log.info("db_pool_ready", min_size=self.min_size, max_size=self.max_size)
                return True
            except Exception as exc:
                log.warning("db_connect_failed", error=str(exc))
                self._pool = None
                return False

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("db_pool_closed")
        self.spool.close()

    async def insert_reading(self, reading: MeterReading) -> None:
        await self._write(KIND_INSERT, _reading_payload(reading))

    async def start_session(self, meter_id: str, session_id: str, start_time: datetime) -> None:
        await self._write(
            KIND_START_SESSION,
            {"meter_id": meter_id, "session_id": session_id, "start_time": start_time.isoformat()},
        )

    async def end_session(
        self, meter_id: str, session_id: str, end_time: datetime, total_cycles: int
    ) -> None:
        await self._write(
            KIND_END_SESSION,
            {
                "meter_id": meter_id,
                "session_id": session_id,
                "end_time": end_time.isoformat(),
                "total_cycles": total_cycles,
            },
        )

    async def open_cycle(
        self, meter_id: str, session_id: str, cycle_id: str, start_time: datetime
    ) -> None:
        await self._write(
            KIND_OPEN_CYCLE,
            {
                "meter_id": meter_id,
                "session_id": session_id,
                "cycle_id": cycle_id,
                "start_time": start_time.isoformat(),
            },
        )

    async def close_cycle(self, meter_id: str, cycle_id: str, end_time: datetime) -> None:
        await self._write(
            KIND_CLOSE_CYCLE,
            {
                "meter_id": meter_id,
                "cycle_id": cycle_id,
                "end_time": end_time.isoformat(),
            },
        )

    async def run_outbox(self, interval: float) -> None:
        """Reconnect + flush spool sampai di-cancel."""
        while True:
            try:
                if await self.ensure_connected():
                    n = await self.flush_outbox()
                    if n:
                        log.info("spool_flushed", count=n, remaining=self.spool.depth)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("spool_maintain_failed")
            await asyncio.sleep(interval)

    async def flush_outbox(self, limit: int = _FLUSH_BATCH) -> int:
        if self._pool is None:
            return 0
        rows = await asyncio.to_thread(self.spool.peek, limit)
        flushed = 0
        for row_id, kind, payload in rows:
            if kind not in (
                KIND_INSERT,
                KIND_START_SESSION,
                KIND_END_SESSION,
                KIND_OPEN_CYCLE,
                KIND_CLOSE_CYCLE,
            ):
                log.error("spool_unknown_kind", kind=kind, id=row_id)
                await asyncio.to_thread(self.spool.delete, row_id)
                continue
            if not await self._apply(kind, payload):
                break
            await asyncio.to_thread(self.spool.delete, row_id)
            flushed += 1
        return flushed

    async def _write(self, kind: str, payload: dict[str, Any]) -> None:
        if await self._apply(kind, payload):
            return
        await asyncio.to_thread(self.spool.enqueue, kind, payload)

    async def _apply(self, kind: str, payload: dict[str, Any]) -> bool:
        if self._pool is None:
            return False
        try:
            if kind == KIND_INSERT:
                reading = _reading_from_payload(payload)
                args = (
                    reading.timestamp,
                    _as_uuid(reading.session_id),
                    _as_uuid(reading.cycle_id),
                    reading.meter_id,
                    reading.device_type,
                    *reading.measurement_tuple(),
                )
                async with self._pool.acquire() as conn:
                    await conn.execute(_INSERT_SQL, *args)
                log.debug(
                    "db_insert_ok",
                    meter_id=reading.meter_id,
                    device_type=reading.device_type,
                    cycle_id=reading.cycle_id,
                    timestamp=reading.timestamp.isoformat(),
                )
                return True

            if kind == KIND_START_SESSION:
                sql, args = _SQL_START_SESSION, (
                    _as_uuid(payload["session_id"]),
                    payload["meter_id"],
                    _dt(payload["start_time"]),
                )
            elif kind == KIND_END_SESSION:
                sql, args = _SQL_END_SESSION, (
                    _as_uuid(payload["session_id"]),
                    _dt(payload["end_time"]),
                )
            elif kind == KIND_OPEN_CYCLE:
                sql, args = _SQL_OPEN_CYCLE, (
                    _as_uuid(payload["cycle_id"]),
                    _as_uuid(payload["session_id"]),
                    payload["meter_id"],
                    _dt(payload["start_time"]),
                )
            elif kind == KIND_CLOSE_CYCLE:
                sql, args = _SQL_CLOSE_CYCLE, (
                    _as_uuid(payload["cycle_id"]),
                    _dt(payload["end_time"]),
                )
            else:
                return False

            async with self._pool.acquire() as conn:
                await conn.execute(sql, *args)
            return True
        except Exception as exc:
            log.error("db_write_failed", op=kind, error=str(exc))
            return False
