"""Ring buffer per meter untuk konsumsi frontend (live view).

Selalu menerima sample tanpa memandang state GPIO. Tidak terlibat sama sekali
dengan alur penyimpanan ke DB.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import List

from models.meter_reading import MeterReading


class RingBuffer:
    """Buffer FIFO thread-safe berbasis `deque(maxlen=...)`.

    Sample tertua otomatis ter-drop saat penuh.
    """

    def __init__(self, maxlen: int):
        self._buf: deque[MeterReading] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, reading: MeterReading) -> None:
        with self._lock:
            self._buf.append(reading)

    def snapshot(self) -> List[MeterReading]:
        """Salinan isi buffer (tidak mengosongkan buffer)."""
        with self._lock:
            return list(self._buf)

    def latest(self) -> MeterReading | None:
        with self._lock:
            return self._buf[-1] if self._buf else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)
