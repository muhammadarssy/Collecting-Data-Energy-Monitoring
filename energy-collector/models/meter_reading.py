"""Payload satu sample pembacaan meter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

# Urutan kolom measurement sesuai schema DB (meter_readings).
# Dipakai juga untuk membangun INSERT statement secara konsisten.
MEASUREMENT_FIELDS: tuple[str, ...] = (
    # Voltage L-L
    "Uab", "Ubc", "Uca",
    # Voltage L-N
    "Ua", "Ub", "Uc",
    # Current
    "Ia", "Ib", "Ic",
    # Active Power
    "Pt", "Pa", "Pb", "Pc",
    # Reactive Power
    "Qt", "Qa", "Qb", "Qc",
    # Power Factor
    "PFt", "PFa", "PFb", "PFc",
    # Frequency & Demand
    "frequency", "active_power_demand",
    # Active Energy
    "ImpEp", "ExpEp",
    # Reactive Energy
    "Q1Eq", "Q2Eq", "Q3Eq", "Q4Eq",
)

DeviceType = Literal["energy", "utils"]


@dataclass
class MeterReading:
    """Satu sample pembacaan dari satu meter.

    Live (buffer/Redis): session_id lifetime aplikasi; energy boleh bawa cycle_id GPIO.
    History DB: session_id lifetime + tanpa cycle_id; interval di poller.
    """

    meter_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: Optional[str] = None
    cycle_id: Optional[str] = None
    device_type: DeviceType = "energy"
    # Nama field -> nilai (float) atau None kalau register error.
    values: dict[str, Optional[float]] = field(default_factory=dict)

    @property
    def is_savable(self) -> bool:
        """Legacy: True kalau sample energy punya session & cycle aktif."""
        if self.device_type != "energy":
            return False
        return self.session_id is not None and self.cycle_id is not None

    def get(self, field_name: str) -> Optional[float]:
        return self.values.get(field_name)

    def measurement_tuple(self) -> tuple[Optional[float], ...]:
        """Nilai measurement terurut sesuai `MEASUREMENT_FIELDS` (untuk INSERT)."""
        return tuple(self.values.get(name) for name in MEASUREMENT_FIELDS)
