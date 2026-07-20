"""Lapisan PostgreSQL via asyncpg.

- Insert per sample (real-time, bukan batch) saat sesi aktif.
- Update tabel tracking `production_sessions` & `production_cycles` saat transisi.
- Semua method menelan error (log saja) supaya tidak mematikan polling loop.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import asyncpg
import structlog

from models.meter_reading import MEASUREMENT_FIELDS, MeterReading

log = structlog.get_logger(__name__)


def _build_insert_sql() -> str:
    cols = ["time", "session_id", "cycle_id", "meter_id", "device_type", *MEASUREMENT_FIELDS]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(cols)
    return f"INSERT INTO meter_readings ({col_list}) VALUES ({placeholders})"


_INSERT_SQL = _build_insert_sql()


def _as_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if value is None:
        return None
    return uuid.UUID(value)


class Database:
    """Wrapper connection pool asyncpg."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self.dsn, min_size=self.min_size, max_size=self.max_size
        )
        log.info("db_pool_ready", min_size=self.min_size, max_size=self.max_size)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("db_pool_closed")

    @property
    def ready(self) -> bool:
        return self._pool is not None

    async def insert_reading(self, reading: MeterReading) -> None:
        if self._pool is None:
            return
        try:
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
        except Exception as exc:
            log.error(
                "db_insert_failed",
                meter_id=reading.meter_id,
                device_type=reading.device_type,
                cycle_id=reading.cycle_id,
                error=str(exc),
            )

    # ── Tracking sesi & cycle ────────────────────────────────
    async def start_session(self, meter_id: str, session_id: str, start_time: datetime) -> None:
        await self._exec(
            "INSERT INTO production_sessions (session_id, meter_id, start_time) "
            "VALUES ($1, $2, $3) ON CONFLICT (session_id) DO NOTHING",
            _as_uuid(session_id), meter_id, start_time,
            label="start_session",
        )

    async def end_session(
        self, meter_id: str, session_id: str, end_time: datetime, total_cycles: int
    ) -> None:
        await self._exec(
            "UPDATE production_sessions SET end_time = $2 WHERE session_id = $1",
            _as_uuid(session_id), end_time,
            label="end_session",
        )

    async def open_cycle(
        self, meter_id: str, session_id: str, cycle_id: str, start_time: datetime
    ) -> None:
        await self._exec(
            "INSERT INTO production_cycles (cycle_id, session_id, meter_id, start_time) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (cycle_id) DO NOTHING",
            _as_uuid(cycle_id), _as_uuid(session_id), meter_id, start_time,
            label="open_cycle",
        )

    async def close_cycle(self, meter_id: str, cycle_id: str, end_time: datetime) -> None:
        await self._exec(
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
            "WHERE pc.cycle_id = $1",
            _as_uuid(cycle_id), end_time,
            label="close_cycle",
        )

    async def _exec(self, sql: str, *args, label: str) -> None:
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(sql, *args)
        except Exception as exc:
            log.error("db_tracking_failed", op=label, error=str(exc))
