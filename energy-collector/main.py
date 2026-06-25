"""Entry point Energy Meter Collection System.

Orkestrasi: load config → init GPIO/DB → spawn polling task per meter →
tangani shutdown bersih (SIGINT/SIGTERM).
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
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
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown = asyncio.Event()

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

        log.info(
            "config_loaded",
            meter_count=len(meters),
            register_count=len(register_map),
            poll_interval_ms=s.poll_interval_ms,
            cycle_timeout_seconds=s.cycle_timeout_seconds,
        )

        # 2. GPIO (RPi.GPIO langsung)
        init_gpio()

        # 3. DB (opsional — kalau gagal connect, jalan tanpa DB / buffer-only)
        self.db = Database(s.db_url)
        try:
            await self.db.connect()
        except Exception as exc:
            log.warning("db_unavailable_running_bufferonly", error=str(exc))
            self.db = None

        # 4. Per meter: buffer → gpio handler → backend → poller
        parser = RegisterParser(register_map)
        force_mock = s.use_mock_hardware

        for meter in meters:
            buffer = RingBuffer(maxlen=s.buffer_maxlen)

            handler = GPIOHandler(
                meter=meter,
                cycle_timeout_seconds=s.cycle_timeout_seconds,
                on_session_start=lambda *a: self._schedule(self._db_start_session, *a),
                on_session_end=lambda *a: self._schedule(self._db_end_session, *a),
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
                db=self.db,
            )
            self.pollers.append(poller)

        log.info(
            "startup_complete",
            meters=[m.id for m in meters],
            gpio_backend="RPi.GPIO",
            db_enabled=self.db is not None,
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
        for h in self.gpio_handlers:
            h.teardown()
        cleanup_gpio()
        if self.db is not None:
            await self.db.close()

    def request_shutdown(self, signame: str) -> None:
        log.info("signal_received", signal=signame)
        self._shutdown.set()

    # ── Coroutine factory untuk hook (dipanggil via run_coroutine_threadsafe) ─
    async def _db_start_session(self, meter_id, session_id, start):
        await self.db.start_session(meter_id, session_id, start)

    async def _db_end_session(self, meter_id, session_id, end, total_cycles):
        await self.db.end_session(meter_id, session_id, end, total_cycles)

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
