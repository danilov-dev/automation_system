"""Канал связи по протоколу Web Socket"""

import asyncio
import logging
from contextlib import suppress
from typing import AsyncIterator, Optional
from websockets.asyncio.server import ServerConnection
from websockets.protocol import State
from .base import ChannelDead
from host.app.protocol import encode


class WsChannel:
    name = "ws"

    def __init__(self, slot_id: str):
        self.slot_id = slot_id
        self._ws: Optional[ServerConnection] = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False

        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
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
    def ws(self) -> Optional[ServerConnection]:
        return self._ws

    @ws.setter
    def ws(self, value: Optional[ServerConnection]) -> None:
        if value is not None or self._ws != value:
            self._ws = value

    async def start(self) -> None:
        """Не открывает порт. Изменяет состояние. Соблюдение контракта наследования"""
        self._started = True
        self._stopped = False
        self.logger.info(f"websocket channel started")

    async def stop(self) -> None:
        """Полная остановка канала (вызывается при stop() слота)."""
        self._stopped = True
        self._started = False
        if self._ws is not None:
            # Принудительно закрываем транспорт
            if hasattr(self._ws, 'transport') and self._ws.transport:
                self._ws.transport.close()
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        await self._incoming.put(None)
        self.logger.info(f"websocket channel stopped")

    async def send(self, msg: dict) -> None:
        """Отправить сообщение"""
        if not self.is_alive:
            raise ChannelDead(f"[{self.slot_id}] ws not alive")
        try:
            await self._ws.send(encode(msg).decode("utf-8"))

            self.logger.debug(f" ws sent {msg}")

        except Exception as e:
            self._ws = None
            raise ChannelDead(f"[{self.slot_id}] ws write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        """Итератор полученных сообщений"""
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    async def attach_ws(self, ws: ServerConnection) -> None:
        """
        Подключение нового ws-соединения.
        Вызывается при каждом новом подключении клиента (включая повторные).
        """
        self.logger.info(f"attaching ws")
        if self._ws is not None and self._ws is not ws:
            with suppress(Exception):
                await self._ws.close()

        self._ws = ws
        self._started = True
        self._stopped = False

    def detach(self) -> None:
        """
        Отключение ws-соединения (обрыв связи).
        НЕ убивает канал полностью - только обнуляет ws.
        Ждет повторного подключения.
        """
        self.logger.info(f"detaching ws")
        self._ws = None

    async def push(self, msg: dict) -> None:
        await self._incoming.put(msg)
