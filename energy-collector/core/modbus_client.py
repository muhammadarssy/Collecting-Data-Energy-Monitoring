"""Polling loop Modbus per meter (asyncio task).

Tiap cycle (default 500ms): baca 2 blok register, parse jadi MeterReading,
push ke ring buffer (selalu), lalu insert ke DB kalau state GPIO aktif.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from config.settings import MeterConfig, RegisterDef
from core.buffer import RingBuffer
from core.gpio_handler import GPIOHandler
from core.hardware.modbus_backend import ModbusBackend, ModbusReadError
from core.register_parser import RegisterParser
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
        gpio_handler: GPIOHandler,
        poll_interval_seconds: float,
        db=None,
    ) -> None:
        self.meter = meter
        self.backend = backend
        self.register_map = register_map
        self.parser = parser
        self.buffer = buffer
        self.gpio = gpio_handler
        self.poll_interval = poll_interval_seconds
        self.db = db

        self._stop = asyncio.Event()
        self._log = log.bind(meter_id=meter.id, port=meter.port)
        self.device_info: dict[str, Optional[float]] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Entry point asyncio task."""
        if not await self._ensure_connected(initial=True):
            self._log.error("modbus_connect_failed_startup")
            return

        await self._read_device_info()

        loop = asyncio.get_event_loop()
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

    async def _poll_once(self) -> None:
        block1 = await self.backend.read_holding_registers(
            BLOCK1_BASE, BLOCK1_COUNT, self.meter.slave_id
        )
        block2 = await self.backend.read_holding_registers(
            BLOCK2_BASE, BLOCK2_COUNT, self.meter.slave_id
        )

        values = self.parser.parse_block(BLOCK1_BASE, block1)
        values.update(self.parser.parse_block(BLOCK2_BASE, block2))

        state, session_id, cycle_id = self.gpio.context()
        reading = MeterReading(
            meter_id=self.meter.id,
            session_id=session_id,
            cycle_id=cycle_id,
            values=values,
        )

        # Selalu ke buffer (untuk frontend), tanpa memandang state.
        self.buffer.push(reading)

        # Ke DB hanya saat sesi aktif.
        if reading.is_savable and self.db is not None:
            await self.db.insert_reading(reading)

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
