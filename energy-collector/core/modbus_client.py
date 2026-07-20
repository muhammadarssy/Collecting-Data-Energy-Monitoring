"""Polling loop Modbus per meter (asyncio task).

Tiap cycle (default 500ms): baca 2 blok register, parse jadi MeterReading,
push ke ring buffer + Redis (selalu).

Persist PostgreSQL:
  - energy — hanya saat state GPIO aktif (session + cycle)
  - utils  — snapshot terbaru tiap UTILS_HISTORY_INTERVAL_SECONDS (tanpa cycle)
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from config.settings import MeterConfig, RegisterDef
from core.buffer import RingBuffer
from core.gpio_handler import GPIOHandler, State
from core.hardware.modbus_backend import ModbusBackend, ModbusReadError
from core.register_parser import RegisterParser
from core.redis_publisher import RedisPublisher
from models.meter_reading import MeterReading

log = structlog.get_logger(__name__)

# Dua blok baca per polling cycle (sesuai plan).
BLOCK1_BASE = 0x2000
BLOCK1_COUNT = 0x2052 - 0x2000  # 82 register (0x2000–0x2051)
BLOCK2_BASE = 0x401E
BLOCK2_COUNT = 0x405A - 0x401E  # 60 register (0x401E–0x4059)

# Backoff reconnect
MAX_RETRY = 3
RETRY_INTERVAL_SECONDS = 2.0


class ModbusPoller:
    """Satu poller per meter."""

    def __init__(
        self,
        meter: MeterConfig,
        backend: ModbusBackend,
        register_map: dict[str, RegisterDef],
        parser: RegisterParser,
        buffer: RingBuffer,
        gpio_handler: Optional[GPIOHandler],
        poll_interval_seconds: float,
        db=None,
        redis: Optional[RedisPublisher] = None,
        utils_history_interval_seconds: float = 300.0,
    ) -> None:
        self.meter = meter
        self.backend = backend
        self.register_map = register_map
        self.parser = parser
        self.buffer = buffer
        self.gpio = gpio_handler
        self.poll_interval = poll_interval_seconds
        self.db = db
        self.redis = redis
        self.utils_history_interval = utils_history_interval_seconds

        self._stop = asyncio.Event()
        self._log = log.bind(
            meter_id=meter.id,
            port=meter.port,
            device_type=meter.device_type,
        )
        self.device_info: dict[str, Optional[float]] = {}
        self._started_monotonic: Optional[float] = None
        self._last_utils_history_at: Optional[float] = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Entry point asyncio task."""
        if not await self._ensure_connected(initial=True):
            self._log.error("modbus_connect_failed_startup")
            return

        self._started_monotonic = asyncio.get_running_loop().time()
        await self._read_device_info()

        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            cycle_start = loop.time()
            try:
                await self._poll_once()
            except ModbusReadError as exc:
                self._log.warning("polling_error", error=str(exc))
                await self._reconnect()
            except Exception:  # jangan biarkan loop mati
                self._log.exception("polling_unexpected_error")

            elapsed = loop.time() - cycle_start
            sleep_for = max(0.0, self.poll_interval - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

        await self.backend.close()
        self._log.info("poller_stopped")

    def _gpio_context(self) -> tuple[str, Optional[str], Optional[str]]:
        if self.gpio is None:
            return State.IDLE.value, None, None
        state, session_id, cycle_id = self.gpio.context()
        return state.value, session_id, cycle_id

    def _running_seconds(self) -> Optional[float]:
        if self._started_monotonic is None:
            return None
        return asyncio.get_running_loop().time() - self._started_monotonic

    async def _poll_once(self) -> None:
        block1 = await self.backend.read_holding_registers(
            BLOCK1_BASE, BLOCK1_COUNT, self.meter.slave_id
        )
        block2 = await self.backend.read_holding_registers(
            BLOCK2_BASE, BLOCK2_COUNT, self.meter.slave_id
        )

        values = self.parser.parse_block(BLOCK1_BASE, block1)
        values.update(self.parser.parse_block(BLOCK2_BASE, block2))

        gpio_state, session_id, cycle_id = self._gpio_context()
        reading = MeterReading(
            meter_id=self.meter.id,
            session_id=session_id,
            cycle_id=cycle_id,
            device_type=self.meter.device_type,
            values=values,
        )

        # Selalu ke buffer (untuk frontend), tanpa memandang state.
        self.buffer.push(reading)

        if self.redis is not None and self.redis.ready:
            # UrAt/IrAt dari cache device_info — tidak dibaca ulang tiap poll.
            await self.redis.publish_reading(
                reading,
                gpio_state=gpio_state if self.meter.is_energy else None,
                device_info=self.device_info,
                running_seconds=self._running_seconds() if self.meter.is_utils else None,
            )

        if self.db is None:
            return

        if self.meter.is_utils:
            await self._maybe_persist_utils_history(reading)
        elif reading.is_savable:
            await self.db.insert_reading(reading)

    async def _maybe_persist_utils_history(self, reading: MeterReading) -> None:
        """Insert 1 snapshot terbaru ke DB tiap interval (tanpa session/cycle)."""
        now = asyncio.get_running_loop().time()
        if (
            self._last_utils_history_at is not None
            and (now - self._last_utils_history_at) < self.utils_history_interval
        ):
            return

        history = MeterReading(
            meter_id=reading.meter_id,
            timestamp=reading.timestamp,
            session_id=None,
            cycle_id=None,
            device_type="utils",
            values=reading.values,
        )
        await self.db.insert_reading(history)
        self._last_utils_history_at = now
        self._log.info(
            "utils_history_persisted",
            interval_seconds=self.utils_history_interval,
            timestamp=history.timestamp.isoformat(),
        )

    async def _read_device_info(self) -> None:
        """Baca register device_info sekali (best-effort)."""
        info_regs = [r for r in self.register_map.values() if r.group == "device_info"]
        if not info_regs:
            return
        try:
            for name, reg in self.register_map.items():
                if reg.group != "device_info":
                    continue
                words = await self.backend.read_holding_registers(
                    reg.address, reg.count, self.meter.slave_id
                )
                parsed = self.parser.parse_block(reg.address, words)
                self.device_info[name] = parsed.get(name)
            self._log.info("device_info_read", **{
                k: v for k, v in self.device_info.items() if v is not None
            })
            if self.redis is not None and self.redis.ready:
                await self.redis.publish_device_info(
                    self.meter.id,
                    self.device_info,
                    device_type=self.meter.device_type,
                )
        except ModbusReadError as exc:
            self._log.warning("device_info_read_failed", error=str(exc))

    async def _ensure_connected(self, initial: bool = False) -> bool:
        if self.backend.connected:
            return True
        for attempt in range(1, MAX_RETRY + 1):
            if self._stop.is_set():
                return False
            try:
                if await self.backend.connect():
                    if not initial:
                        self._log.info("reconnect_success", attempt=attempt)
                    return True
            except Exception as exc:  # pragma: no cover
                self._log.warning("connect_attempt_failed", attempt=attempt, error=str(exc))
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        return False

    async def _reconnect(self) -> None:
        try:
            await self.backend.close()
        except Exception:  # pragma: no cover
            pass
        if not await self._ensure_connected():
            self._log.error("reconnect_failed_max_retry", max_retry=MAX_RETRY)
            return
        await self._read_device_info()
