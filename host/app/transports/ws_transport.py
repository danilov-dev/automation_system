"""Транспорт связи по протоколу WebSocket."""

import asyncio
import logging
from contextlib import suppress
from typing import AsyncIterator

from websockets.asyncio.server import ServerConnection
from websockets.protocol import State

from host.app.transports.base import TransportDead
from host.app.protocol import encode


class WsTransport:
    """WebSocket транспорт с поддержкой маркировки для тестирования."""
    name = "ws"

    def __init__(self, slot_id: str):
        self.slot_id = slot_id
        self._ws: ServerConnection | None = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False
        self._testing = False  # Занят ли тестированием
        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        """Физическое состояние соединения."""
        return (
                self._started
                and not self._stopped
                and self._ws is not None
                and self._ws.state is State.OPEN
        )

    @is_alive.setter
    def is_alive(self, value: bool) -> None:
        if not value:
            self._started = False
            self._stopped = True

    @property
    def is_available(self) -> bool:
        """Доступен ли транспорт (не занят тестом и физически жив)."""
        return self.is_alive and not self._testing

    @property
    def ws(self) -> ServerConnection | None:
        return self._ws

    @ws.setter
    def ws(self, value: ServerConnection | None) -> None:
        if value is not None or self._ws != value:
            self._ws = value

    def mark_for_testing(self) -> None:
        """Пометить транспорт как занятый тестированием."""
        self._testing = True
        self.logger.info(f"[{self.slot_id}] ws marked for testing")

    def release_from_testing(self) -> None:
        """Освободить транспорт после тестирования."""
        self._testing = False
        self.logger.info(f"[{self.slot_id}] ws released from testing")

    async def start(self) -> None:
        """Не открывает порт. Изменяет состояние. Соблюдение контракта наследования."""
        self._started = True
        self._stopped = False
        self.logger.info(f"[{self.slot_id}] websocket transport started")

    async def stop(self) -> None:
        """Полная остановка транспорта."""
        self._stopped = True
        self._started = False
        if self._ws is not None:
            if hasattr(self._ws, 'transport') and self._ws.transport:
                self._ws.transport.close()
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        await self._incoming.put(None)
        self.logger.info(f"[{self.slot_id}] websocket transport stopped")

    async def send(self, msg: dict) -> None:
        """Отправить сообщение."""
        if not self.is_alive:
            raise TransportDead(f"[{self.slot_id}] ws not alive")
        try:
            await self._ws.send(encode(msg).decode("utf-8"))
            self.logger.debug(f"[{self.slot_id}] ws sent {msg}")
        except Exception as e:
            self._ws = None
            raise TransportDead(f"[{self.slot_id}] ws write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        """Итератор полученных сообщений."""
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    async def attach_ws(self, ws: ServerConnection) -> None:
        """Подключение нового ws-соединения."""
        self.logger.info(f"[{self.slot_id}] attaching ws")
        if self._ws is not None and self._ws is not ws:
            with suppress(Exception):
                await self._ws.close()
        self._ws = ws
        self._started = True
        self._stopped = False

    def detach(self) -> None:
        """Отключение ws-соединения (обрыв связи)."""
        self.logger.info(f"[{self.slot_id}] detaching ws")
        self._ws = None

    async def push(self, msg: dict) -> None:
        """Поместить сообщение во входящую очередь."""
        await self._incoming.put(msg)
