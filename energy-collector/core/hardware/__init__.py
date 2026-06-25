"""Hardware abstraction layer — Modbus backend (asli / mock)."""
from .modbus_backend import ModbusBackend, create_modbus_backend

__all__ = [
    "ModbusBackend",
    "create_modbus_backend",
]
