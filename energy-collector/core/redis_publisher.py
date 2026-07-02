"""Publish sample buffer & device info ke Redis untuk konsumsi service API terpisah.

Key pattern (multi-collector, multi-meter):
  {prefix}:{collector_id}:meter:{meter_id}:latest
  {prefix}:{collector_id}:meter:{meter_id}:readings
  {prefix}:{collector_id}:meter:{meter_id}:device_info
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis
import structlog

from models.meter_reading import MeterReading

log = structlog.get_logger(__name__)

# CT/PT ratio — di-inject dari cache device_info (baca sekali saat startup/reconnect,
# tidak ikut polling 500ms) agar backend bisa skala tegangan/arus/energi.
_RATIO_FIELDS: tuple[str, ...] = ("UrAt", "IrAt")


def _serialize_reading(
    reading: MeterReading,
    collector_id: str,
    gpio_state: Optional[str] = None,
    device_info: Optional[dict[str, Optional[float]]] = None,
) -> str:
    values = dict(reading.values)
    if device_info:
        for key in _RATIO_FIELDS:
            val = device_info.get(key)
            if val is not None:
                values[key] = val
    payload = {
        "collector_id": collector_id,
        "meter_id": reading.meter_id,
        "timestamp": reading.timestamp.isoformat(),
        "session_id": reading.session_id,
        "cycle_id": reading.cycle_id,
        "values": values,
    }
    if gpio_state is not None:
        payload["gpio_state"] = gpio_state
    return json.dumps(payload, default=str)


class RedisPublisher:
    """Async Redis client untuk mirror ring buffer & device info."""

    def __init__(
        self,
        redis_url: str,
        collector_id: str,
        buffer_maxlen: int,
        device_info_ttl_seconds: int = 86400,
        key_prefix: str = "energy",
    ) -> None:
        self.redis_url = redis_url
        self.collector_id = collector_id
        self.buffer_maxlen = buffer_maxlen
        self.device_info_ttl_seconds = device_info_ttl_seconds
        self.key_prefix = key_prefix
        self._client: Optional[redis.Redis] = None

    def _key(self, meter_id: str, suffix: str) -> str:
        return f"{self.key_prefix}:{self.collector_id}:meter:{meter_id}:{suffix}"

    async def connect(self) -> bool:
        try:
            self._client = redis.from_url(self.redis_url, decode_responses=True)
            await self._client.ping()
            log.info(
                "redis_connected",
                collector_id=self.collector_id,
                key_prefix=self.key_prefix,
            )
            return True
        except Exception as exc:
            log.warning("redis_unavailable", error=str(exc))
            if self._client is not None:
                await self._client.aclose()
            self._client = None
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info("redis_closed")

    @property
    def ready(self) -> bool:
        return self._client is not None

    async def publish_reading(
        self,
        reading: MeterReading,
        gpio_state: Optional[str] = None,
        device_info: Optional[dict[str, Optional[float]]] = None,
    ) -> None:
        if not self.ready:
            return
        try:
            body = _serialize_reading(
                reading, self.collector_id, gpio_state, device_info
            )
            key_latest = self._key(reading.meter_id, "latest")
            key_readings = self._key(reading.meter_id, "readings")
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.set(key_latest, body)
                pipe.lpush(key_readings, body)
                pipe.ltrim(key_readings, 0, self.buffer_maxlen - 1)
                await pipe.execute()
        except Exception:  # pragma: no cover
            log.exception("redis_publish_reading_failed", meter_id=reading.meter_id)

    async def publish_device_info(
        self,
        meter_id: str,
        info: dict[str, Optional[float]],
    ) -> None:
        if not self.ready or not info:
            return
        try:
            key = self._key(meter_id, "device_info")
            mapping = {
                k: "" if v is None else str(v)
                for k, v in info.items()
            }
            mapping["collector_id"] = self.collector_id
            mapping["meter_id"] = meter_id
            mapping["updated_at"] = datetime.now(timezone.utc).isoformat()
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.hset(key, mapping=mapping)
                pipe.expire(key, self.device_info_ttl_seconds)
                await pipe.execute()
            log.debug("redis_device_info_published", meter_id=meter_id)
        except Exception:  # pragma: no cover
            log.exception("redis_publish_device_info_failed", meter_id=meter_id)
