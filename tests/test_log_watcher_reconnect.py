import asyncio
from unittest.mock import AsyncMock

import serial

from app.log_watcher import LogWatcher


class BrokenSerial:
    is_open = True
    write_async = AsyncMock()

    @property
    def in_waiting(self):
        raise serial.SerialException("device disconnected")

    def close(self):
        self.is_open = False


class HealthySerial:
    def __init__(self):
        self.is_open = True
        self.write_async = AsyncMock()

    @property
    def in_waiting(self):
        return 0

    async def read_async(self, _size):
        return b""

    def close(self):
        self.is_open = False


async def test_watcher_reopens_port_after_read_disconnect(monkeypatch):
    opened_healthy = asyncio.Event()
    attempts = 0
    healthy = HealthySerial()

    def serial_factory(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return BrokenSerial()
        opened_healthy.set()
        return healthy

    monkeypatch.setattr("app.log_watcher.aioserial.AioSerial", serial_factory)
    watcher = LogWatcher(
        "slot1",
        "/dev/fake",
        reconnect_initial_delay=0.01,
        reconnect_max_delay=0.02,
    )
    await watcher.start()
    try:
        await asyncio.wait_for(opened_healthy.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        assert attempts >= 2
        assert watcher.is_alive
    finally:
        await watcher.stop()
