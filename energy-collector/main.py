"""Entry point Energy Meter Collection System.

Orkestrasi: load config → init GPIO/DB → spawn session per meter →
polling task → tangani shutdown bersih (SIGINT/SIGTERM).

Session per meter hidup dari start sampai shutdown; GPIO energy hanya cycle.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Optional

import structlog

from config.settings import get_settings, load_meters, load_registers
from core.buffer import RingBuffer
from core.db import Database
from core.gpio_handler import GPIOHandler, cleanup_gpio, init_gpio
from core.hardware import create_modbus_backend
from core.modbus_client import ModbusPoller
from core.register_parser import RegisterParser
from core.redis_publisher import RedisPublisher

log = structlog.get_logger("main")


def setup_logging(level: str) -> None:
    """Konfigurasi structlog → output JSON ke stdout."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class Application:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.pollers: list[ModbusPoller] = []
        self.gpio_handlers: list[GPIOHandler] = []
        self.db: Optional[Database] = None
        self.redis: Optional[RedisPublisher] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown = asyncio.Event()
        self._gpio_initialized = False

    # ── Hook DB dari thread GPIO → schedule ke event loop ────
    def _schedule(self, coro_factory, *args) -> None:
        if self.db is None or not self.db.ready or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(coro_factory(*args), self._loop)
        except Exception:  # pragma: no cover
            log.exception("schedule_db_hook_failed")

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        s = self.settings

        # 1. Load config
        try:
            meters = load_meters(s.resolve_path(s.meters_config_path))
            register_map = load_registers(s.resolve_path(s.register_map_path))
        except Exception as exc:
            log.error("config_invalid", error=str(exc))
            raise SystemExit(1)

        energy_meters = [m for m in meters if m.is_energy]
        utils_meters = [m for m in meters if m.is_utils]

        log.info(
            "config_loaded",
            meter_count=len(meters),
            energy_count=len(energy_meters),
            utils_count=len(utils_meters),
            register_count=len(register_map),
            poll_interval_ms=s.poll_interval_ms,
            cycle_timeout_seconds=s.cycle_timeout_seconds,
            utils_history_interval_seconds=s.utils_history_interval_seconds,
        )

        # 2. GPIO hanya jika ada meter energy
        if energy_meters:
            init_gpio()
            self._gpio_initialized = True

        # 3. DB (opsional — kalau gagal connect, jalan tanpa DB / buffer-only)
        self.db = Database(s.db_url)
        try:
            await self.db.connect()
        except Exception as exc:
            log.warning("db_unavailable_running_bufferonly", error=str(exc))
            self.db = None

        # 3b. Redis (opsional — mirror buffer & device info untuk service API)
        if s.redis_url:
            if not s.collector_id.strip():
                log.warning(
                    "redis_collector_id_missing",
                    hint="Set COLLECTOR_ID di .env agar key Redis unik per device collector",
                )
            else:
                self.redis = RedisPublisher(
                    redis_url=s.redis_url,
                    collector_id=s.collector_id.strip(),
                    buffer_maxlen=s.buffer_maxlen,
                    device_info_ttl_seconds=s.device_info_ttl_seconds,
                    key_prefix=s.redis_key_prefix,
                )
                if not await self.redis.connect():
                    self.redis = None

        # 4. Per meter: session lifetime → gpio (energy) → backend → poller
        parser = RegisterParser(register_map)
        force_mock = s.use_mock_hardware
        session_start = datetime.now(timezone.utc)

        for meter in meters:
            buffer = RingBuffer(maxlen=s.buffer_maxlen)
            handler: Optional[GPIOHandler] = None
            session_id = str(uuid.uuid4())

            if self.db is not None:
                await self.db.start_session(meter.id, session_id, session_start)
            log.info("session_start", meter_id=meter.id, session_id=session_id)

            if meter.is_energy:
                handler = GPIOHandler(
                    meter=meter,
                    session_id=session_id,
                    on_cycle_open=lambda *a: self._schedule(self._db_open_cycle, *a),
                    on_cycle_close=lambda *a: self._schedule(self._db_close_cycle, *a),
                )
                handler.setup()
                self.gpio_handlers.append(handler)

            backend = create_modbus_backend(meter, register_map, force_mock=force_mock)
            poller = ModbusPoller(
                meter=meter,
                backend=backend,
                register_map=register_map,
                parser=parser,
                buffer=buffer,
                gpio_handler=handler,
                poll_interval_seconds=s.poll_interval_seconds,
                session_id=session_id,
                db=self.db,
                redis=self.redis,
                utils_history_interval_seconds=s.utils_history_interval_seconds,
            )
            self.pollers.append(poller)

        log.info(
            "startup_complete",
            meters=[{"id": m.id, "type": m.device_type} for m in meters],
            gpio_enabled=self._gpio_initialized,
            db_enabled=self.db is not None,
            redis_enabled=self.redis is not None and self.redis.ready,
            collector_id=s.collector_id.strip() or None,
        )

        # 5. Jalankan semua poller sampai shutdown
        tasks = [asyncio.create_task(p.run(), name=f"poller-{p.meter.id}") for p in self.pollers]
        await self._shutdown.wait()

        log.info("shutdown_started")
        for p in self.pollers:
            p.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._cleanup()
        log.info("shutdown_complete")

    async def _cleanup(self) -> None:
        # Tutup cycle terbuka dulu (await), lalu end session lifetime
        for h in self.gpio_handlers:
            pending = h.teardown()
            if pending is not None and self.db is not None:
                meter_id, cycle_id, end = pending
                await self.db.close_cycle(meter_id, cycle_id, end)
        if self._gpio_initialized:
            cleanup_gpio()

        if self.db is not None:
            end = datetime.now(timezone.utc)
            gpio_by_meter = {h.meter.id: h for h in self.gpio_handlers}
            for p in self.pollers:
                handler = gpio_by_meter.get(p.meter.id)
                total_cycles = handler.cycle_count if handler is not None else 0
                await self.db.end_session(p.meter.id, p.session_id, end, total_cycles)
                log.info(
                    "session_end",
                    meter_id=p.meter.id,
                    session_id=p.session_id,
                    total_cycles=total_cycles,
                )

        if self.redis is not None:
            await self.redis.close()
        if self.db is not None:
            await self.db.close()

    def request_shutdown(self, signame: str) -> None:
        log.info("signal_received", signal=signame)
        self._shutdown.set()

    # ── Coroutine factory untuk hook cycle (dari thread GPIO) ─
    async def _db_open_cycle(self, meter_id, session_id, cycle_id, start):
        await self.db.open_cycle(meter_id, session_id, cycle_id, start)

    async def _db_close_cycle(self, meter_id, cycle_id, end):
        await self.db.close_cycle(meter_id, cycle_id, end)


async def _amain() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = Application()

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, partial(app.request_shutdown, signame))
        except NotImplementedError:
            # Windows: add_signal_handler tidak didukung → fallback signal.signal
            signal.signal(sig, lambda *_: app.request_shutdown(signame))

    await app.start()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
