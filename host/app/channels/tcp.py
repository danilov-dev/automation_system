"""
TcpChannel — транспорт поверх уже установленного TCP-соединения.

В отличие от SerialChannel, здесь канал НЕ открывает сеть сам.
TCP-сервер общий (TcpRouter), writer приходит извне через attach_writer().
Канал — это «логическая привязка» slot_id <--> активное соединение.

Контракт Channel:
  - send()        — писать в текущий writer или ChannelDead
  - incoming()    — очередь сообщений от этого PC (переживает переподключения)
  - is_alive      — есть ли сейчас writer и не закрыт ли он
"""
import asyncio
import time
from contextlib import suppress
from datetime import datetime
from typing import AsyncIterator, Optional

from .base import ChannelDead
from host.app.protocol import encode
from host.app.utils.console_logger import ConsoleLogger, LogLevel


class TcpChannel:
    name = "connection"

    def __init__(self, slot_id: str):
        self.slot_id = slot_id
        self._writer: Optional[asyncio.StreamWriter] = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False

        self._last_heartbeat_time: float = 0.0
        self.heartbeat_timeout = 5


    @property
    def is_alive(self) -> bool:
        if not (
                self._started
                and not self._stopped
                and self._writer is not None
                and not self._writer.is_closing()
        ):
            return False

        if self._last_heartbeat_time > 0:
            elapsed = time.monotonic() - self._last_heartbeat_time
            if elapsed > self.heartbeat_timeout:
                ConsoleLogger.write(
                    f"[{self.slot_id}] TCP heartbeat timeout: "
                    f"no heartbeat for {elapsed:.0f}s",
                    LogLevel.WARNING,
                )
                return False

        return True

    async def start(self) -> None:
        """Изменение состояния порта. Соблюдение контракта наследования"""
        self._started = True
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self._writer is not None:
            with suppress(Exception):
                self._writer.close()
            self._writer = None
        await self._incoming.put(None)


    async def send(self, msg: dict) -> None:
        """Отправить сообщение"""
        if not self.is_alive:
            raise ChannelDead(f"[{self.slot_id}] connection not alive")
        try:
            self._writer.write(encode(msg))
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            self._writer = None
            raise ChannelDead(f"[{self.slot_id}] connection write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        """Итератор обработанных сообщений """
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg


    async def attach_writer(self, writer: asyncio.StreamWriter) -> None:
        """Подписывает новое подключение к плате"""
        if self._writer is not None and self._writer is not writer:
            with suppress(Exception):
                self._writer.close()
        self._writer = writer

    def detach(self) -> None:
        """Отписывает плату от подключения. Не закрывается, а ждет переподключения"""
        self._writer = None

    async def push(self, msg: dict) -> None:
        """Роутер кладёт расшифрованные сообщения от клиента."""
        await self._incoming.put(msg)


    # Добавил сердцебиение для того, чтобы клиент не умирал
    async def send_heartbeat(self):
        """Отправить heartbeat клиенту."""
        if not self.is_alive:
            return
        try:
            heartbeat = {"type": "heartbeat", "timestamp": datetime.now().isoformat()}
            if self._writer is not None:
                self._writer.write(encode(heartbeat))
                await self._writer.drain()
                ConsoleLogger.write(
                    f"[{self.slot_id}] heartbeat send",
                    LogLevel.INFO
                )
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.slot_id}] heartbeat send failed: {e}",
                LogLevel.WARNING
            )