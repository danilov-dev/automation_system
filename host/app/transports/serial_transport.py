# host/app/transports/serial_transport.py
"""Serial транспорт с поддержкой маркировки для тестирования."""
import asyncio
import logging
from typing import AsyncIterator

import serial
import serial_asyncio

from host.app.transports.base import TransportDead
from host.app.protocol import encode, decode


class SerialTransport:
    """Serial транспорт с поддержкой маркировки для тестирования."""
    name = "serial"

    def __init__(self, slot_id: str, port: str, baudrate: int = 115200):
        self.slot_id = slot_id
        self.port = port
        self.baudrate = baudrate
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False
        self._testing = False  # Занят ли тестированием
        self._read_task: asyncio.Task | None = None
        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        """Физическое состояние порта."""
        return (
            self._started
            and not self._stopped
            and self._writer is not None
            and not self._writer.is_closing()
        )

    @property
    def is_available(self) -> bool:
        """Доступен ли транспорт (не занят тестом и физически жив)."""
        return self.is_alive and not self._testing

    def mark_for_testing(self) -> None:
        """Пометить транспорт как занятый тестированием."""
        self._testing = True
        self.logger.info(f"[{self.slot_id}] serial marked for testing")

    def release_from_testing(self) -> None:
        """Освободить транспорт после тестирования."""
        self._testing = False
        self.logger.info(f"[{self.slot_id}] serial released from testing")

    async def start(self) -> None:
        """Открыть serial порт и запустить чтение."""
        if self._started:
            return

        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self.port,
                baudrate=self.baudrate,
            )
            self._started = True
            self._stopped = False
            self._read_task = asyncio.create_task(
                self._read_loop(),
                name=f"serial-read-{self.slot_id}"
            )
            self.logger.info(
                f"[{self.slot_id}] serial transport started on {self.port}"
            )
        except Exception as e:
            self.logger.error(
                f"[{self.slot_id}] failed to start serial on {self.port}: {e}"
            )
            raise

    async def stop(self) -> None:
        """Закрыть serial порт и остановить чтение."""
        self._stopped = True
        self._started = False

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        await self._incoming.put(None)
        self.logger.info(f"[{self.slot_id}] serial transport stopped")

    async def send(self, msg: dict) -> None:
        """Отправить сообщение."""
        if not self.is_alive:
            raise TransportDead(f"[{self.slot_id}] serial not alive")
        try:
            data = encode(msg) + b"\n"
            self._writer.write(data)
            await self._writer.drain()
            self.logger.debug(f"[{self.slot_id}] serial sent {msg}")
        except Exception as e:
            raise TransportDead(f"[{self.slot_id}] serial write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        """Итератор полученных сообщений."""
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    async def _read_loop(self) -> None:
        """Цикл чтения из serial порта."""
        try:
            while self.is_alive:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = decode(line)
                    if msg:
                        await self._incoming.put(msg)
                except Exception as e:
                    self.logger.warning(
                        f"[{self.slot_id}] serial decode error: {e}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"[{self.slot_id}] serial read loop error: {e}")

    async def push(self, msg: dict) -> None:
        """Поместить сообщение во входящую очередь."""
        await self._incoming.put(msg)