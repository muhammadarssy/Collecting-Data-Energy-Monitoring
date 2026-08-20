"""Konfigurasi global aplikasi.

Parameter dibaca dari environment / file `.env` via pydantic-settings.
Definisi meter dan register map dimuat dari file YAML (lazy, via fungsi loader).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Root project (folder yang memuat main.py)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Parameter global dari `.env` / environment."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    db_url: str = Field(
        default="postgresql://user:pass@localhost:5432/energy_db",
        alias="DB_URL",
    )
    buffer_maxlen: int = Field(default=240, alias="BUFFER_MAXLEN")
    cycle_timeout_seconds: float = Field(default=300.0, alias="CYCLE_TIMEOUT_SECONDS")
    poll_interval_ms: int = Field(default=500, alias="POLL_INTERVAL_MS")
    # Interval persist history ke PostgreSQL (energy & utils). Default 300 = 5 menit.
    # Reading history memakai session_id lifetime, tanpa cycle_id.
    # GPIO energy (jika ENABLE_GPIO) hanya untuk tabel production_cycles.
    utils_history_interval_seconds: float = Field(
        default=300.0, alias="UTILS_HISTORY_INTERVAL_SECONDS"
    )

    meters_config_path: str = Field(default="config/meters.yaml", alias="METERS_CONFIG_PATH")
    register_map_path: str = Field(default="config/registers.yaml", alias="REGISTER_MAP_PATH")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Redis — opsional; mirror buffer & device info untuk service API terpisah
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")
    collector_id: str = Field(default="", alias="COLLECTOR_ID")
    device_info_ttl_seconds: int = Field(default=86400, alias="DEVICE_INFO_TTL_SECONDS")
    redis_key_prefix: str = Field(default="energy", alias="REDIS_KEY_PREFIX")

    # None = auto-detect (pymodbus ada -> hardware asli, kalau tidak -> mock)
    use_mock_hardware: Optional[bool] = Field(default=None, alias="USE_MOCK_HARDWARE")

    # None = auto: false di Windows, true di Linux/Pi. false = tanpa GPIO/cycle.
    enable_gpio: Optional[bool] = Field(default=None, alias="ENABLE_GPIO")

    @property
    def poll_interval_seconds(self) -> float:
        return self.poll_interval_ms / 1000.0

    @property
    def gpio_enabled(self) -> bool:
        """GPIO + production_cycles. Auto-off di Windows kecuali ENABLE_GPIO=true."""
        if self.enable_gpio is not None:
            return self.enable_gpio
        return sys.platform != "win32"

    def resolve_path(self, path_str: str) -> Path:
        """Resolusi path relatif terhadap root project."""
        p = Path(path_str)
        return p if p.is_absolute() else (BASE_DIR / p)


class MeterConfig(BaseModel):
    """Konfigurasi satu meter dari `meters.yaml`.

    `type` / `device_type`:
      - energy — gpio_pin wajib jika ENABLE_GPIO; session lifetime; GPIO track cycle
      - utils  — tanpa GPIO/cycle; session lifetime; history tiap interval
    Jika ENABLE_GPIO=false (default Windows), energy tetap poll + history, tanpa cycle.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    port: str
    baudrate: int = 9600
    slave_id: int = 1
    parity: Literal["N", "E", "O"] = "N"
    stopbits: int = 1
    bytesize: int = 8
    device_type: Literal["energy", "utils"] = Field(default="energy", alias="type")
    gpio_pin: Optional[int] = None
    gpio_debounce_ms: int = 50
    # HIGH harus stabil selama ini agar cycle ditutup (pulse HIGH lebih pendek = noise).
    gpio_noise_delay_ms: int = 500
    # LOW terus-menerus selama ini = mesin mati; HIGH berikutnya reset standby.
    gpio_standby_low_seconds: float = 30.0

    @model_validator(mode="after")
    def _validate_gpio_for_type(self) -> MeterConfig:
        if self.device_type == "utils" and self.gpio_pin is not None:
            raise ValueError(f"meter '{self.id}' type=utils tidak boleh punya gpio_pin")
        return self

    @property
    def is_energy(self) -> bool:
        return self.device_type == "energy"

    @property
    def is_utils(self) -> bool:
        return self.device_type == "utils"


class RegisterDef(BaseModel):
    """Definisi satu register dari `registers.yaml`."""

    address: int
    type: Literal["float32", "int16", "uint16"]
    count: int = 1
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    scale: float = 1.0
    unit: str = ""
    group: str = "misc"
    note: Optional[str] = None

    @field_validator("address", mode="before")
    @classmethod
    def _parse_address(cls, v):
        # Dukung "0x2000" (str) maupun int.
        if isinstance(v, str):
            return int(v, 0)
        return v


def load_meters(path: Path, require_gpio: bool = True) -> list[MeterConfig]:
    """Muat daftar meter dari YAML. Raise kalau file/format invalid.

    require_gpio: True di Pi — meter energy wajib gpio_pin unik.
    False (Windows / ENABLE_GPIO=false) — gpio_pin opsional dan diabaikan.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw or "meters" not in raw:
        raise ValueError(f"File meter '{path}' tidak punya key 'meters'")
    meters = [MeterConfig(**m) for m in raw["meters"]]
    if not meters:
        raise ValueError(f"File meter '{path}' kosong")
    _assert_unique([m.id for m in meters], "id meter")
    _assert_unique([m.port for m in meters], "port serial")
    gpio_pins = [m.gpio_pin for m in meters if m.gpio_pin is not None]
    _assert_unique(gpio_pins, "gpio_pin")
    if require_gpio:
        missing = [m.id for m in meters if m.is_energy and m.gpio_pin is None]
        if missing:
            raise ValueError(
                f"meter energy wajib gpio_pin jika ENABLE_GPIO: {missing}"
            )
    return meters


def load_registers(path: Path) -> dict[str, RegisterDef]:
    """Muat register map dari YAML. Raise kalau file/format invalid."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw or "registers" not in raw:
        raise ValueError(f"File register '{path}' tidak punya key 'registers'")
    return {name: RegisterDef(**cfg) for name, cfg in raw["registers"].items()}


def _assert_unique(values: list, label: str) -> None:
    seen = set()
    dupes = {v for v in values if v in seen or seen.add(v)}
    if dupes:
        raise ValueError(f"Nilai {label} duplikat: {sorted(dupes)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
