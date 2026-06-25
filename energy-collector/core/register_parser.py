"""Decode raw Modbus register (list 16-bit words) menjadi nilai numerik.

Versi-independen dari pymodbus: decoding murni pakai `struct`, jadi aman dari
deprecation `BinaryPayloadDecoder` di pymodbus 3.9+.

Konvensi:
- Setiap register Modbus = 16-bit word.
- `byte_order`: urutan byte DI DALAM satu register. `big` = MSB dulu (standar
  Modbus), `little` = byte ditukar.
- `word_order`: urutan register untuk nilai multi-register (float32 = 2 word).
  `big` = word pertama adalah high word.
"""
from __future__ import annotations

import struct
from typing import Optional

import structlog

from config.settings import RegisterDef

log = structlog.get_logger(__name__)


def _words_to_bytes(words: list[int], byte_order: str) -> bytes:
    fmt = ">H" if byte_order == "big" else "<H"
    out = bytearray()
    for w in words:
        out += struct.pack(fmt, w & 0xFFFF)
    return bytes(out)


def decode_register(words: list[int], reg: RegisterDef) -> Optional[float]:
    """Decode satu nilai dari list word mentah. Return None kalau gagal."""
    try:
        if len(words) < reg.count:
            return None

        ordered = list(words[: reg.count])
        if reg.word_order == "little":
            ordered = list(reversed(ordered))

        raw = _words_to_bytes(ordered, reg.byte_order)

        if reg.type == "float32":
            value = struct.unpack(">f", raw[:4])[0]
        elif reg.type == "int16":
            value = struct.unpack(">h", raw[:2])[0]
        elif reg.type == "uint16":
            value = struct.unpack(">H", raw[:2])[0]
        else:  # pragma: no cover - dicegah validator pydantic
            return None

        # NaN / inf dianggap pembacaan tidak valid.
        if value != value or value in (float("inf"), float("-inf")):
            return None

        return float(value) * reg.scale
    except (struct.error, ValueError, TypeError) as exc:
        log.warning("register_decode_failed", register=reg.address, error=str(exc))
        return None


class RegisterParser:
    """Decode blok response Modbus berdasarkan register map.

    Sebuah "blok" adalah hasil satu request `read_holding_registers` yang
    mengembalikan list word berurutan mulai dari `base_address`.
    """

    def __init__(self, register_map: dict[str, RegisterDef]):
        self.register_map = register_map

    def parse_block(self, base_address: int, words: list[int]) -> dict[str, Optional[float]]:
        """Ekstrak semua register yang alamatnya jatuh di dalam blok ini."""
        result: dict[str, Optional[float]] = {}
        end = base_address + len(words)
        for name, reg in self.register_map.items():
            if base_address <= reg.address < end:
                offset = reg.address - base_address
                if offset + reg.count > len(words):
                    result[name] = None
                    continue
                slice_ = words[offset : offset + reg.count]
                result[name] = decode_register(slice_, reg)
        return result
