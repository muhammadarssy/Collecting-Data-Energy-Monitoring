"""State machine sesi & cycle per pin GPIO (per meter).

Optocoupler open-collector: pin default HIGH (idle), LOW = mesin aktif.

  IDLE --falling--> SAVING --rising--> COOLING --falling--> SAVING ...
                                          \--timeout--> IDLE

Transisi memanggil hook (on_session_start/end, on_cycle_open/close) yang
di-wire oleh main.py untuk menulis ke tabel tracking DB. Hook dipanggil dari
thread polling GPIO, jadi implementasinya harus thread-safe / non-blocking.

Pembacaan GPIO mengikuti pola gpio_reader.py: BCM, setup INPUT tanpa pull-up,
poll level via GPIO.input() di thread terpisah.
"""
from __future__ import annotations

import enum
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import RPi.GPIO as GPIO  # type: ignore
import structlog

from config.settings import MeterConfig

log = structlog.get_logger(__name__)

# BCM pin yang bentrok dengan interface bawaan Pi.
_I2C_PINS = frozenset({2, 3})
_SPI_PINS = frozenset({7, 8, 9, 10, 11})
_UART_PINS = frozenset({14, 15})


class State(str, enum.Enum):
    IDLE = "IDLE"
    SAVING = "SAVING"
    COOLING = "COOLING"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _gpio_setup_hints(pin: int) -> list[str]:
    hints = [
        "Pastikan tidak ada proses lain yang memakai pin ini (cek: sudo lsof /dev/gpiochip*).",
        "Pastikan user ada di grup gpio: sudo usermod -aG gpio $USER",
    ]
    if pin in _I2C_PINS:
        hints.insert(
            0,
            f"GPIO {pin} adalah pin I2C (SDA/SCL). Nonaktifkan I2C di raspi-config "
            "atau pindah ke pin lain (mis. 14, 17, 27).",
        )
    if pin in _SPI_PINS:
        hints.insert(0, f"GPIO {pin} bentrok dengan SPI — nonaktifkan SPI atau ganti pin.")
    if pin in _UART_PINS:
        hints.insert(0, f"GPIO {pin} bentrok dengan UART — nonaktifkan serial console atau ganti pin.")
    return hints


def init_gpio() -> None:
    """Inisialisasi global RPi.GPIO (panggil sekali saat startup)."""
    GPIO.setmode(GPIO.BCM)
    log.info("gpio_init", mode="BCM")


def cleanup_gpio() -> None:
    """Bersihkan semua pin GPIO (panggil saat shutdown)."""
    try:
        GPIO.cleanup()
    except Exception:
        pass


# Tipe hook (semua argumen keyword-friendly via posisi yang stabil).
SessionStartHook = Callable[[str, str, datetime], None]          # meter_id, session_id, start
SessionEndHook = Callable[[str, str, datetime, int], None]       # meter_id, session_id, end, total_cycles
CycleOpenHook = Callable[[str, str, str, datetime], None]        # meter_id, session_id, cycle_id, start
CycleCloseHook = Callable[[str, str, datetime], None]            # meter_id, cycle_id, end


class GPIOHandler:
    """Kelola satu pin dan state sesi/cycle-nya."""

    def __init__(
        self,
        meter: MeterConfig,
        cycle_timeout_seconds: float,
        on_session_start: Optional[SessionStartHook] = None,
        on_session_end: Optional[SessionEndHook] = None,
        on_cycle_open: Optional[CycleOpenHook] = None,
        on_cycle_close: Optional[CycleCloseHook] = None,
    ) -> None:
        self.meter = meter
        self.pin = meter.gpio_pin
        self.cycle_timeout_seconds = cycle_timeout_seconds

        self._on_session_start = on_session_start
        self._on_session_end = on_session_end
        self._on_cycle_open = on_cycle_open
        self._on_cycle_close = on_cycle_close

        self._lock = threading.RLock()
        self._state = State.IDLE
        self._session_id: Optional[str] = None
        self._cycle_id: Optional[str] = None
        self._cycle_start: Optional[datetime] = None
        self._session_start: Optional[datetime] = None
        self._cycle_count = 0
        self._timeout_timer: Optional[threading.Timer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._last_level: Optional[int] = None
        self._last_edge_time = 0.0
        self._debounce_seconds = 0.05

        self._log = log.bind(meter_id=meter.id, gpio_pin=self.pin)

    # ── Setup / teardown ─────────────────────────────────────
    def setup(self) -> None:
        debounce_ms = max(1, self.meter.gpio_debounce_ms)
        self._debounce_seconds = debounce_ms / 1000.0

        GPIO.setup(self.pin, GPIO.IN)

        if not self._start_polling(debounce_ms):
            hints = _gpio_setup_hints(self.pin)
            self._log.error("gpio_setup_failed", hints=hints)
            raise RuntimeError(
                f"GPIO pin {self.pin} ({self.meter.id}): gagal setup polling"
            )

        level = GPIO.input(self.pin)
        self._log.info(
            "gpio_handler_ready",
            state=self._state.value,
            level="LOW" if level == GPIO.LOW else "HIGH",
            gpio_level=level,
        )

    def _start_polling(self, debounce_ms: int) -> bool:
        """Poll level pin di thread terpisah (sama seperti gpio_reader.py)."""
        try:
            self._last_level = GPIO.input(self.pin)
        except Exception as exc:
            self._log.error("gpio_poll_init_failed", error=str(exc))
            return False

        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name=f"gpio-poll-{self.meter.id}",
            daemon=True,
        )
        self._poll_thread.start()
        self._log.info("gpio_polling_started", debounce_ms=debounce_ms)
        return True

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            try:
                level = GPIO.input(self.pin)
            except Exception:  # pragma: no cover
                self._log.exception("gpio_poll_input_failed")
                if self._poll_stop.wait(0.05):
                    return
                continue

            if level != self._last_level:
                now = time.monotonic()
                if now - self._last_edge_time >= self._debounce_seconds:
                    self._last_edge_time = now
                    self._last_level = level
                    self._on_edge(self.pin)
                else:
                    self._last_level = level

            if self._poll_stop.wait(0.01):
                return

    def teardown(self) -> None:
        with self._lock:
            self._cancel_timer()
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=1.0)

    # ── Akses thread-safe untuk polling loop ─────────────────
    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def context(self) -> tuple[State, Optional[str], Optional[str]]:
        """Snapshot atomik (state, session_id, cycle_id)."""
        with self._lock:
            return self._state, self._session_id, self._cycle_id

    @property
    def is_saving(self) -> bool:
        with self._lock:
            return self._state in (State.SAVING, State.COOLING)

    # ── Callback edge GPIO ───────────────────────────────────
    def _on_edge(self, channel) -> None:
        try:
            level = GPIO.input(self.pin)
        except Exception:  # pragma: no cover
            self._log.exception("gpio_input_failed")
            return
        with self._lock:
            state_before = self._state.value
        self._log.info(
            "gpio_edge",
            level="LOW" if level == GPIO.LOW else "HIGH",
            gpio_level=level,
            state=state_before,
        )
        if level == GPIO.LOW:
            self._handle_falling()
        else:
            self._handle_rising()

    def _handle_falling(self) -> None:
        """HIGH -> LOW: mulai sesi / tutup cycle lama buka cycle baru."""
        with self._lock:
            if self._state == State.IDLE:
                self._start_session()
            elif self._state == State.COOLING:
                self._cancel_timer()
                self._close_cycle()
                self._open_cycle()
                self._state = State.SAVING
            # SAVING + falling: abaikan (sudah aktif)

    def _handle_rising(self) -> None:
        """LOW -> HIGH: masuk fase cooling, mulai timeout."""
        with self._lock:
            if self._state == State.SAVING:
                self._state = State.COOLING
                self._start_timer()

    def _on_timeout(self) -> None:
        """HIGH terlalu lama -> tutup cycle & sesi, kembali IDLE."""
        with self._lock:
            if self._state != State.COOLING:
                return
            self._close_cycle()
            self._end_session()
            self._state = State.IDLE

    # ── Transisi internal (dipanggil saat lock dipegang) ─────
    def _start_session(self) -> None:
        self._session_id = _new_id()
        self._session_start = _now()
        self._cycle_count = 0
        self._state = State.SAVING
        self._log.info("session_start", session_id=self._session_id)
        self._safe_hook(self._on_session_start, self.meter.id, self._session_id, self._session_start)
        self._open_cycle()

    def _open_cycle(self) -> None:
        self._cycle_id = _new_id()
        self._cycle_start = _now()
        self._cycle_count += 1
        self._log.info("cycle_open", session_id=self._session_id, cycle_id=self._cycle_id)
        self._safe_hook(
            self._on_cycle_open, self.meter.id, self._session_id, self._cycle_id, self._cycle_start
        )

    def _close_cycle(self) -> None:
        if self._cycle_id is None:
            return
        end = _now()
        duration = (end - self._cycle_start).total_seconds() if self._cycle_start else 0.0
        self._log.info(
            "cycle_close", cycle_id=self._cycle_id, duration_seconds=round(duration, 2)
        )
        self._safe_hook(self._on_cycle_close, self.meter.id, self._cycle_id, end)
        self._cycle_id = None
        self._cycle_start = None

    def _end_session(self) -> None:
        if self._session_id is None:
            return
        end = _now()
        duration = (end - self._session_start).total_seconds() if self._session_start else 0.0
        self._log.info(
            "session_end",
            session_id=self._session_id,
            total_cycles=self._cycle_count,
            duration_seconds=round(duration, 2),
        )
        self._safe_hook(
            self._on_session_end, self.meter.id, self._session_id, end, self._cycle_count
        )
        self._session_id = None
        self._session_start = None

    # ── Timer ────────────────────────────────────────────────
    def _start_timer(self) -> None:
        self._cancel_timer()
        self._timeout_timer = threading.Timer(self.cycle_timeout_seconds, self._on_timeout)
        self._timeout_timer.daemon = True
        self._timeout_timer.start()

    def _cancel_timer(self) -> None:
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

    @staticmethod
    def _safe_hook(hook, *args) -> None:
        if hook is None:
            return
        try:
            hook(*args)
        except Exception:  # pragma: no cover
            log.exception("gpio_hook_error")
