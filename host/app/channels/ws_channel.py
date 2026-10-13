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
            and self._ws is not None
            and self._ws.state is State.OPEN
        )

    async def start(self) -> None:
        self._started = True
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        await self._incoming.put(None)

    async def send(self, msg: dict) -> None:
        if not self.is_alive:
            raise ChannelDead(f"[{self.pc_id}] ws not alive")
        try:
            await self._ws.send(encode(msg).decode("utf-8"))
        except Exception as e:
            self._ws = None
            raise ChannelDead(f"[{self.pc_id}] ws write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    # ── внутреннее API для WsRouter ───────────────────────
    async def attach_ws(self, ws: ServerConnection) -> None:
        if self._ws is not None and self._ws is not ws:
            with suppress(Exception):
                await self._ws.close()
        self._ws = ws

    def detach(self) -> None:
        self._ws = None

    async def push(self, msg: dict) -> None:
        await self._incoming.put(msg)