"""Konfigurasi global aplikasi.

Parameter dibaca dari environment / file `.env` via pydantic-settings.
Definisi meter dan register map dimuat dari file YAML (lazy, via fungsi loader).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
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

    @property
    def poll_interval_seconds(self) -> float:
        return self.poll_interval_ms / 1000.0

    def resolve_path(self, path_str: str) -> Path:
        """Resolusi path relatif terhadap root project."""
        p = Path(path_str)
        return p if p.is_absolute() else (BASE_DIR / p)


class MeterConfig(BaseModel):
    """Konfigurasi satu meter dari `meters.yaml`."""

    id: str
    label: str
    port: str
    baudrate: int = 9600
    slave_id: int = 1
    parity: Literal["N", "E", "O"] = "N"
    stopbits: int = 1
    bytesize: int = 8
    gpio_pin: int
    gpio_debounce_ms: int = 50


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


def load_meters(path: Path) -> list[MeterConfig]:
    """Muat daftar meter dari YAML. Raise kalau file/format invalid."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw or "meters" not in raw:
        raise ValueError(f"File meter '{path}' tidak punya key 'meters'")
    meters = [MeterConfig(**m) for m in raw["meters"]]
    if not meters:
        raise ValueError(f"File meter '{path}' kosong")
    # Validasi: id, port, dan gpio_pin harus unik
    _assert_unique([m.id for m in meters], "id meter")
    _assert_unique([m.port for m in meters], "port serial")
    _assert_unique([m.gpio_pin for m in meters], "gpio_pin")
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
