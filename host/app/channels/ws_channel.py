"""WebSocket channel implementing the common transport contract."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import AsyncIterator

from websockets.asyncio.server import ServerConnection
from websockets.protocol import State

from .base import ChannelDead
from .incoming import IncomingMessageQueue
from host.app.protocol import encode


class WsChannel:
    name = "ws"

    def __init__(self, slot_id: str, incoming_queue_size: int = 1024) -> None:
        self.slot_id = slot_id
        self._ws: ServerConnection | None = None
        self.incoming_queue_size = incoming_queue_size
        self._incoming = IncomingMessageQueue(incoming_queue_size)
        self._started = False
        self._stopped = False
        self._send_lock = asyncio.Lock()
        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        return (
            self._started
            and not self._stopped
            and self._ws is not None
            and self._ws.state is State.OPEN
        )

    @property
    def ws(self) -> ServerConnection | None:
        return self._ws

    async def start(self) -> None:
        if self._started and not self._stopped:
            return
        self._incoming = IncomingMessageQueue(self.incoming_queue_size)
        self._started = True
        self._stopped = False
        self.logger.info("WebSocket channel started")

    async def stop(self) -> None:
        self._stopped = True
        self._started = False
        ws = self._ws
        self._ws = None
        if ws is not None:
            with suppress(Exception):
                await ws.close()
        await self._incoming.close()
        self.logger.info("WebSocket channel stopped")

    async def send(self, msg: dict) -> None:
        async with self._send_lock:
            ws = self._ws
            if not self.is_alive or ws is None:
                raise ChannelDead(f"[{self.slot_id}] WebSocket channel is not alive")
            try:
                await ws.send(encode(msg).decode("utf-8"))
            except Exception as exc:
                if self._ws is ws:
                    self._ws = None
                raise ChannelDead(
                    f"[{self.slot_id}] WebSocket send failed: {exc}"
                ) from exc

    async def incoming(self) -> AsyncIterator[dict]:
        async for message in self._incoming.iterate():
            yield message

    async def attach_ws(self, ws: ServerConnection) -> None:
        if self._stopped:
            await self.start()
        previous = self._ws
        if previous is not None and previous is not ws:
            with suppress(Exception):
                await previous.close()
        self._ws = ws
        self._started = True
        self._stopped = False
        self.logger.info("WebSocket attached")

    def detach(self, ws: ServerConnection | None = None) -> None:
        """Detach only the expected connection, protecting a newer reconnect."""
        if ws is not None and self._ws is not ws:
            return
        self._ws = None
        self.logger.info("WebSocket detached")

    async def push(self, msg: dict) -> None:
        if not self._stopped:
            await self._incoming.put(msg)
