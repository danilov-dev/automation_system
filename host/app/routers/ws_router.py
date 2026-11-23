"""WebSocket server routing one client connection to each slot channel."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve

from host.app.channels.ws_channel import WsChannel
from host.app.protocol import decode


logger = logging.getLogger(__name__)


class WsRouter:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        hello_timeout: float = 5.0,
        ping_interval: float = 10.0,
        ping_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.hello_timeout = hello_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._channels: dict[str, WsChannel] = {}
        self._server: Server | None = None
        self.is_started = False

    def register(self, channel: WsChannel) -> None:
        if channel.slot_id in self._channels:
            raise ValueError(f"slot_id {channel.slot_id!r} is already registered")
        self._channels[channel.slot_id] = channel

    @property
    def actual_port(self) -> int:
        if self._server is not None:
            sockets = self._server.sockets or []
            if sockets:
                return sockets[0].getsockname()[1]
        return self.port

    async def start(self) -> None:
        if self.is_started:
            return
        self._server = await serve(
            self._handler,
            self.host,
            self.port,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        )
        self.is_started = True
        logger.info("WebSocket server listening on %s:%s", self.host, self.actual_port)

    async def stop(self) -> None:
        server = self._server
        if server is None:
            self.is_started = False
            return
        server.close()
        await server.wait_closed()
        self._server = None
        self.is_started = False
        for channel in self._channels.values():
            channel.detach()
        logger.info("WebSocket server stopped")

    async def _handler(self, ws: ServerConnection) -> None:
        remote = ws.remote_address
        channel: WsChannel | None = None
        try:
            channel = await self._handshake(ws, remote)
            if channel is None:
                await ws.close(1008, "invalid hello")
                return
            await self._pump(ws, channel)
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            logger.info("[WS] %s connection closed: %s", remote, exc)
        except Exception:
            logger.exception("[WS] %s handler failed", remote)
        finally:
            if channel is not None:
                channel.detach(ws)

    async def _handshake(
        self,
        ws: ServerConnection,
        remote: object,
    ) -> WsChannel | None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.hello_timeout)
        except asyncio.TimeoutError:
            logger.warning("[WS] %s did not send hello within %.1fs", remote, self.hello_timeout)
            return None
        except websockets.ConnectionClosed:
            return None

        message = decode(raw)
        if not message or message.get("type") != "hello":
            logger.warning("[WS] %s sent an invalid hello frame", remote)
            return None

        slot_id = message.get("slot_id")
        channel = self._channels.get(slot_id)
        if channel is None:
            logger.warning("[WS] %s requested unknown slot_id=%r", remote, slot_id)
            return None

        await channel.attach_ws(ws)
        logger.info("[WS] slot %s connected from %s", slot_id, remote)
        return channel

    async def _pump(self, ws: ServerConnection, channel: WsChannel) -> None:
        async for raw in ws:
            message = decode(raw)
            if message is not None:
                await channel.push(message)
