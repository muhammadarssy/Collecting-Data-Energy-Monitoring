"""State machine cycle per pin GPIO (per meter).

Optocoupler open-collector: pin default HIGH (idle), LOW = mesin aktif.

  IDLE --LOW--> SAVING --HIGH stabil >= noise_delay--> IDLE (1 cycle)
                  \\--HIGH singkat--> tetap SAVING (noise, digabung)
                  \\--LOW >= standby_low--> abort; HIGH --> IDLE (standby)

Session hidup selama proses collector; GPIO hanya buka/tutup cycle.
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

_I2C_PINS = frozenset({2, 3})
_SPI_PINS = frozenset({7, 8, 9, 10, 11})
_UART_PINS = frozenset({14, 15})


class State(str, enum.Enum):
    IDLE = "IDLE"
    SAVING = "SAVING"
    ABORT_WAIT = "ABORT_WAIT"  # LOW terlalu lama; tunggu HIGH → standby


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
    GPIO.setmode(GPIO.BCM)
    log.info("gpio_init", mode="BCM")


def cleanup_gpio() -> None:
    try:
        GPIO.cleanup()
    except Exception:
        pass


CycleOpenHook = Callable[[str, str, str, datetime], None]
CycleCloseHook = Callable[[str, str, datetime], None]


class GPIOHandler:
    """Kelola satu pin dan cycle-nya di dalam session aplikasi."""

    def __init__(
        self,
        meter: MeterConfig,
        session_id: str,
        on_cycle_open: Optional[CycleOpenHook] = None,
        on_cycle_close: Optional[CycleCloseHook] = None,
    ) -> None:
        self.meter = meter
        if meter.gpio_pin is None:
            raise ValueError(f"GPIOHandler butuh gpio_pin untuk meter '{meter.id}'")
        self.pin = meter.gpio_pin
        self._session_id = session_id

        self._on_cycle_open = on_cycle_open
        self._on_cycle_close = on_cycle_close

        self._lock = threading.RLock()
        self._state = State.IDLE
        self._cycle_id: Optional[str] = None
        self._cycle_start: Optional[datetime] = None
        self._cycle_count = 0
        self._end_timer: Optional[threading.Timer] = None
        self._standby_timer: Optional[threading.Timer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._last_level: Optional[int] = None
        self._last_change_mono = 0.0
        self._debounce_seconds = 0.05
        self._noise_delay_seconds = 0.5
        self._standby_low_seconds = 30.0

        self._log = log.bind(meter_id=meter.id, gpio_pin=self.pin, session_id=session_id)

    def setup(self) -> None:
        debounce_ms = max(1, self.meter.gpio_debounce_ms)
        noise_ms = max(1, self.meter.gpio_noise_delay_ms)
        standby_s = max(1.0, float(self.meter.gpio_standby_low_seconds))
        self._debounce_seconds = debounce_ms / 1000.0
        self._noise_delay_seconds = noise_ms / 1000.0
        self._standby_low_seconds = standby_s

        GPIO.setup(self.pin, GPIO.IN)

        if not self._start_polling(debounce_ms, noise_ms, standby_s):
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
            noise_delay_ms=noise_ms,
            standby_low_seconds=standby_s,
        )

    def _start_polling(self, debounce_ms: int, noise_ms: int, standby_s: float) -> bool:
        try:
            self._last_level = GPIO.input(self.pin)
            self._last_change_mono = time.monotonic()
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
        self._log.info(
            "gpio_polling_started",
            debounce_ms=debounce_ms,
            noise_delay_ms=noise_ms,
            standby_low_seconds=standby_s,
        )
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

            now = time.monotonic()
            if level != self._last_level:
                # debounce: abaikan perubahan lebih cepat dari debounce
                if now - self._last_change_mono >= self._debounce_seconds:
                    prev = self._last_level
                    self._last_level = level
                    self._last_change_mono = now
                    if prev is not None:
                        self._on_level(level)

            if self._poll_stop.wait(0.01):
                return

    def teardown(self) -> Optional[tuple[str, str, datetime]]:
        pending: Optional[tuple[str, str, datetime]] = None
        with self._lock:
            self._cancel_end_timer()
            self._cancel_standby_timer()
            if self._cycle_id is not None:
                end = _now()
                pending = (self.meter.id, self._cycle_id, end)
                self._log.info("cycle_close", cycle_id=self._cycle_id, reason="teardown")
                self._cycle_id = None
                self._cycle_start = None
                self._state = State.IDLE
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=1.0)
        return pending

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return self._cycle_count

    def context(self) -> tuple[State, Optional[str], Optional[str]]:
        with self._lock:
            return self._state, self._session_id, self._cycle_id

    @property
    def is_saving(self) -> bool:
        with self._lock:
            return self._state == State.SAVING

    def _on_level(self, level: int) -> None:
        with self._lock:
            state_before = self._state.value
        self._log.info(
            "gpio_edge",
            level="LOW" if level == GPIO.LOW else "HIGH",
            gpio_level=level,
            state=state_before,
        )
        if level == GPIO.LOW:
            self._handle_low()
        else:
            self._handle_high()

    def _handle_low(self) -> None:
        """LOW: mulai cycle dari IDLE, atau batalkan end-timer (noise HIGH singkat)."""
        with self._lock:
            if self._state == State.IDLE:
                self._open_cycle()
                self._state = State.SAVING
                self._start_standby_timer()
            elif self._state == State.SAVING:
                # HIGH noise berakhir → lanjut cycle, restart timer LOW-mati
                self._cancel_end_timer()
                self._start_standby_timer()
            # ABORT_WAIT + LOW: tetap tunggu HIGH

    def _handle_high(self) -> None:
        """HIGH: jika noise → end-timer; jika abort wait → standby IDLE."""
        with self._lock:
            if self._state == State.SAVING:
                self._cancel_standby_timer()
                # HIGH singkat = noise; tutup cycle hanya jika stabil >= noise_delay
                self._start_end_timer()
            elif self._state == State.ABORT_WAIT:
                self._state = State.IDLE
                self._log.info("standby_reset", reason="machine_off_low_then_high")

    def _on_end_timer(self) -> None:
        """HIGH stabil cukup lama → 1 cycle selesai."""
        with self._lock:
            if self._state != State.SAVING or self._cycle_id is None:
                return
            # pastikan pin masih HIGH
            try:
                if GPIO.input(self.pin) != GPIO.HIGH:
                    return
            except Exception:
                return
            self._close_cycle()
            self._state = State.IDLE
            self._cancel_standby_timer()

    def _on_standby_timer(self) -> None:
        """LOW terus-menerus terlalu lama → mesin mati, abort cycle."""
        with self._lock:
            if self._state != State.SAVING:
                return
            try:
                if GPIO.input(self.pin) != GPIO.LOW:
                    return
            except Exception:
                return
            if self._cycle_id is not None:
                # batalkan cycle (tutup DB agar tidak menggantung), kurangi hitungan produksi
                cid = self._cycle_id
                end = _now()
                self._log.info(
                    "cycle_abort",
                    cycle_id=cid,
                    reason="standby_low",
                    standby_low_seconds=self._standby_low_seconds,
                )
                self._safe_hook(self._on_cycle_close, self.meter.id, cid, end)
                self._cycle_id = None
                self._cycle_start = None
                if self._cycle_count > 0:
                    self._cycle_count -= 1
            self._cancel_end_timer()
            self._state = State.ABORT_WAIT

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

    def _start_end_timer(self) -> None:
        self._cancel_end_timer()
        self._end_timer = threading.Timer(self._noise_delay_seconds, self._on_end_timer)
        self._end_timer.daemon = True
        self._end_timer.start()

    def _cancel_end_timer(self) -> None:
        if self._end_timer is not None:
            self._end_timer.cancel()
            self._end_timer = None

    def _start_standby_timer(self) -> None:
        self._cancel_standby_timer()
        self._standby_timer = threading.Timer(
            self._standby_low_seconds, self._on_standby_timer
        )
        self._standby_timer.daemon = True
        self._standby_timer.start()

    def _cancel_standby_timer(self) -> None:
        if self._standby_timer is not None:
            self._standby_timer.cancel()
            self._standby_timer = None

    @staticmethod
    def _safe_hook(hook, *args) -> None:
        if hook is None:
            return
        try:
            hook(*args)
        except Exception:  # pragma: no cover
            log.exception("gpio_hook_error")
