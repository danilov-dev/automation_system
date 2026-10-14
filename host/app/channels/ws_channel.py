import asyncio
from contextlib import suppress
from typing import AsyncIterator, Optional

from websockets.asyncio.server import ServerConnection
from websockets.protocol import State

from .base import ChannelDead
from host.app.protocol import encode


class WsChannel:
    name = "ws"

    def __init__(self, pc_id: str):
        self.pc_id = pc_id
        self._ws: Optional[ServerConnection] = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False

    @property
    def is_alive(self) -> bool:
        return (
                self._started
                and not self._stopped
                and self.ws is not None
                and self.ws.state is State.OPEN
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
    def ws(self, value: ServerConnection) -> None:
        if self._ws != value:
            self._ws = value


    async def start(self) -> None:
        self._started = True
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self.ws is not None:
            with suppress(Exception):
                await self.ws.close()
            self.ws = None
        await self._incoming.put(None)

    async def send(self, msg: dict) -> None:
        if not self.is_alive:
            raise ChannelDead(f"[{self.pc_id}] ws not alive")
        try:
            await self.ws.send(encode(msg).decode("utf-8"))
        except Exception as e:
            self.ws = None
            raise ChannelDead(f"[{self.pc_id}] ws write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    # ── внутреннее API для WsRouter ───────────────────────
    async def attach_ws(self, ws: ServerConnection) -> None:
        if self.ws is not None and self.ws is not ws:
            with suppress(Exception):
                await self.ws.close()
        self.ws = ws

    def detach(self) -> None:
        self.ws = None
        self._started = False
        self._stopped = True
        with suppress(Exception):
            self._incoming.put_nowait(None)

    async def push(self, msg: dict) -> None:
        await self._incoming.put(msg)