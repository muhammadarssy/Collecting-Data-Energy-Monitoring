"""Backend Modbus: pymodbus AsyncModbusSerialClient asli atau mock.

Interface seragam (`ModbusBackend`) supaya `modbus_client.py` tidak peduli
apakah sedang bicara ke hardware asli atau data sintetis.
"""
from __future__ import annotations

import math
import random
import struct
import time
from abc import ABC, abstractmethod
from typing import Optional

import structlog

from config.settings import MeterConfig, RegisterDef

log = structlog.get_logger(__name__)


class ModbusReadError(Exception):
    """Dilempar saat satu request Modbus gagal/timeout."""


class ModbusBackend(ABC):
    """Kontrak minimal yang dipakai polling loop."""

    @abstractmethod
    async def connect(self) -> bool:
        ...

    @abstractmethod
    async def read_holding_registers(self, address: int, count: int, slave: int) -> list[int]:
        """Return list 16-bit word. Raise ModbusReadError kalau gagal."""

    @abstractmethod
    async def close(self) -> None:
        ...

    @property
    @abstractmethod
    def connected(self) -> bool:
        ...


def create_modbus_backend(
    meter: MeterConfig,
    register_map: dict[str, RegisterDef],
    force_mock: Optional[bool] = None,
) -> ModbusBackend:
    """Pilih backend Modbus.

    force_mock: True=mock, False=wajib asli, None=auto (coba import pymodbus).
    """
    if force_mock is True:
        return MockModbusBackend(meter, register_map)

    try:
        from pymodbus.client import AsyncModbusSerialClient  # noqa: F401

        return PymodbusBackend(meter)
    except ImportError as exc:
        if force_mock is False:
            raise RuntimeError("pymodbus wajib tapi tidak terpasang") from exc
        log.info("modbus_backend_selected", backend="mock", reason="auto", detail=str(exc))
        return MockModbusBackend(meter, register_map)


class PymodbusBackend(ModbusBackend):
    """Wrapper AsyncModbusSerialClient (RTU)."""

    _PARITY_MAP = {"N": "N", "E": "E", "O": "O"}

    def __init__(self, meter: MeterConfig):
        from pymodbus.client import AsyncModbusSerialClient

        self.meter = meter
        self._client = AsyncModbusSerialClient(
            port=meter.port,
            baudrate=meter.baudrate,
            bytesize=meter.bytesize,
            parity=self._PARITY_MAP.get(meter.parity, "N"),
            stopbits=meter.stopbits,
            timeout=1.0,
        )

    async def connect(self) -> bool:
        ok = await self._client.connect()
        return bool(ok)

    @property
    def connected(self) -> bool:
        return bool(getattr(self._client, "connected", False))

    async def read_holding_registers(self, address: int, count: int, slave: int) -> list[int]:
        try:
            rr = await self._client.read_holding_registers(address=address, count=count, slave=slave)
        except Exception as exc:  # pymodbus melempar berbagai exception
            raise ModbusReadError(f"read error @0x{address:04X}: {exc}") from exc
        if rr is None or rr.isError():
            raise ModbusReadError(f"modbus error response @0x{address:04X}: {rr}")
        return list(rr.registers)

    async def close(self) -> None:
        self._client.close()


class MockModbusBackend(ModbusBackend):
    """Hasilkan data register sintetis yang realistis (float32 big/big)."""

    def __init__(self, meter: MeterConfig, register_map: dict[str, RegisterDef]):
        self.meter = meter
        self.register_map = register_map
        self._connected = False
        self._t0 = time.time()
        # Counter energi yang akumulatif.
        self._imp_energy = random.uniform(1000.0, 5000.0)

    async def connect(self) -> bool:
        self._connected = True
        return True

    @property
    def connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        self._connected = False

    async def read_holding_registers(self, address: int, count: int, slave: int) -> list[int]:
        if not self._connected:
            raise ModbusReadError("mock backend belum connect")

        values = self._generate_values()
        words = [0] * count
        end = address + count
        for name, reg in self.register_map.items():
            if not (address <= reg.address < end):
                continue
            offset = reg.address - address
            val = values.get(name, 0.0)
            self._place(words, offset, reg, val)
        return words

    def _place(self, words: list[int], offset: int, reg: RegisterDef, value: float) -> None:
        if reg.type == "float32" and offset + 1 < len(words):
            hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
            words[offset] = hi
            words[offset + 1] = lo
        elif offset < len(words):
            words[offset] = int(value) & 0xFFFF

    def _generate_values(self) -> dict[str, float]:
        t = time.time() - self._t0
        # Variasi halus berbasis sinus + noise.
        base_v = 220.0 + 3.0 * math.sin(t / 5.0)

        def v():
            return base_v + random.uniform(-1.5, 1.5)

        ua, ub, uc = v(), v(), v()
        ia = 10.0 + 2.0 * math.sin(t / 3.0) + random.uniform(-0.3, 0.3)
        ib = 10.0 + 2.0 * math.sin(t / 3.0 + 2.0) + random.uniform(-0.3, 0.3)
        ic = 10.0 + 2.0 * math.sin(t / 3.0 + 4.0) + random.uniform(-0.3, 0.3)
        pf = 0.92 + random.uniform(-0.03, 0.03)
        pa, pb, pc = ua * ia * pf, ub * ib * pf, uc * ic * pf
        qa, qb, qc = (
            ua * ia * math.sqrt(max(0.0, 1 - pf**2)),
            ub * ib * math.sqrt(max(0.0, 1 - pf**2)),
            uc * ic * math.sqrt(max(0.0, 1 - pf**2)),
        )
        pt = pa + pb + pc
        # Akumulasi energi (kWh) berdasarkan daya sesaat.
        self._imp_energy += pt / 1000.0 * 0.5 / 3600.0

        return {
            "Uab": ua * math.sqrt(3), "Ubc": ub * math.sqrt(3), "Uca": uc * math.sqrt(3),
            "Ua": ua, "Ub": ub, "Uc": uc,
            "Ia": ia, "Ib": ib, "Ic": ic,
            "Pt": pt, "Pa": pa, "Pb": pb, "Pc": pc,
            "Qt": qa + qb + qc, "Qa": qa, "Qb": qb, "Qc": qc,
            "PFt": pf, "PFa": pf, "PFb": pf, "PFc": pf,
            "frequency": 50.0 + random.uniform(-0.05, 0.05),
            "active_power_demand": pt * random.uniform(0.95, 1.0),
            "ImpEp": self._imp_energy, "ExpEp": 0.0,
            "Q1Eq": self._imp_energy * 0.3, "Q2Eq": 0.0, "Q3Eq": 0.0, "Q4Eq": 0.0,
            "network_mode": 0.0, "meter_type": 1.0,
            "slave_id_register": float(self.meter.slave_id),
            "baudrate_register": float(self.meter.baudrate),
        }
