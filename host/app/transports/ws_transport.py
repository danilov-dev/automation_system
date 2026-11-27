"""Транспорт связи по протоколу WebSocket."""

import asyncio
import logging
from contextlib import suppress
from typing import AsyncIterator

from websockets.asyncio.server import ServerConnection
from websockets.protocol import State

from host.app.transports.base import TransportDead
from host.app.protocol import encode


class WebSocketTransport:
    """WebSocket транспорт с поддержкой маркировки для тестирования."""
    name = "ws"

    def __init__(self, slot_id: str):
        self.slot_id = slot_id
        self._ws: ServerConnection | None = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False
        self._testing = False
        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        return self._started and not self._stopped and self._ws is not None and self._ws.state is State.OPEN

    @is_alive.setter
    def is_alive(self, value: bool) -> None:
        if not value:
            self._started = False
            self._stopped = True

    @property
    def is_available(self) -> bool:
        return self.is_alive and not self._testing

    @property
    def ws(self) -> ServerConnection | None:
        return self._ws

    @ws.setter
    def ws(self, value: ServerConnection | None) -> None:
        self._ws = value

    def mark_for_testing(self) -> None:
        self._testing = True

    def release_from_testing(self) -> None:
        self._testing = False

    async def start(self) -> None:
        self._started = True
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        self._started = False
        if self._ws is not None:
            if self._ws.transport:
                self._ws.transport.close()
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        await self._incoming.put(None)

    async def send(self, msg: dict) -> None:
        if not self.is_alive:
            raise TransportDead(f"[{self.slot_id}] ws not alive")
        try:
            await self._ws.send(encode(msg).decode("utf-8"))
        except Exception as exc:
            self._ws = None
            raise TransportDead(f"[{self.slot_id}] ws write failed: {exc}") from exc

    async def incoming(self) -> AsyncIterator[dict]:
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    async def attach_ws(self, ws: ServerConnection) -> None:
        if self._ws is not None and self._ws is not ws:
            with suppress(Exception):
                await self._ws.close()
        self._ws = ws
        self._started = True
        self._stopped = False

    def detach(self) -> None:
        self._ws = None

    async def push(self, msg: dict) -> None:
        await self._incoming.put(msg)


# Compatibility alias for code using the shorter name.
WsTransport = WebSocketTransport
