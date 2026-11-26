import asyncio
import logging
from typing import Protocol, runtime_checkable, Optional

import serial


@runtime_checkable
class PowerTransport(Protocol):
    """Интерфейс транспорта управления питанием"""
    async def set_power(self, state: bool) -> None: ...
    async def close(self) -> None: ...

class SerialRtsTransport:
    def __init__(self, port: str, baudrate: int = 9600, retries: int = 3) -> None:
        self.port = port
        self.baudrate = baudrate
        self.retries = retries
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()

        self.logger = logging.getLogger("SerialRtsTransport")

    def _open_port(self) -> serial.Serial:
        return serial.Serial(self.port, self.baudrate, timeout=1)

    async def set_power(self, state: bool) -> None:
        async with self._lock:
            last_err: Optional[Exception] = None
            for attempt in range(self.retries):
                try:
                    if self._ser is None or not self._ser.is_open:
                        self._ser = self._open_port()
                    self._ser.setRTS(state)
                    return
                except (serial.SerialException, OSError) as e:
                    last_err = e
                    self.logger.warning(
                        "RTS set failed (attempt %d/%d): %s",
                        attempt + 1, self.retries, e,
                    )
                    # закрываем битый объект перед следующей попыткой
                    if self._ser is not None:
                        try:
                            self._ser.close()
                        except Exception:
                            pass
                        self._ser = None
                    await asyncio.sleep(0.2 * (attempt + 1))   # backoff
            raise RuntimeError(
                f"Failed to set RTS={state} on {self.port} after {self.retries} attempts"
            ) from last_err

    async def close(self) -> None:
        async with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None