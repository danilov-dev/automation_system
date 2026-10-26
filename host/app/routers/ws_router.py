"""
WsRouter — единый WebSocket-сервер для всех слотов.
"""
import asyncio
from contextlib import suppress
from typing import Dict, Optional
import websockets
from websockets.asyncio.server import Server, serve
from .base_router import BaseRouter
from host.app.channels.ws_channel import WsChannel
from host.app.protocol import decode
from host.app.utils.console_logger import ConsoleLogger, LogLevel


class WsRouter(BaseRouter):
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        hello_timeout: float = 5.0,
        ping_interval: float = 10.0,
        ping_timeout: float = 10.0,
    ):
        super().__init__(host=host, port=port, hello_timeout=hello_timeout)
        self._channels: Dict[str, WsChannel] = {}
        self._server: Optional[Server] = None
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

    # ── реестр каналов ────────────────────────────────────
    def register(self, channel: WsChannel) -> None:
        if channel.slot_id in self._channels:
            raise ValueError(f"slot_id '{channel.slot_id}' уже зарегистрирован")
        self._channels[channel.slot_id] = channel

    @property
    def actual_port(self) -> int:
        if self._server is not None:
            sockets = getattr(self._server, "sockets", None) or []
            if sockets:
                return sockets[0].getsockname()[1]
        return self.port

    async def start(self) -> None:
        """Создает сервер."""
        self._server = await serve(
            self._handler,
            self.host,
            self.port,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        )
        self.is_started = True
        ConsoleLogger.write(
            f"[WS] сервер слушает ws://{self.host}:{self.actual_port}",
            LogLevel.SUCCESS,
        )

    async def stop(self) -> None:
        if self._server:
            self._server.close()

            for ch in self._channels.values():
                if ch.ws is not None:
                    try:
                        if hasattr(ch.ws, 'transport') and ch.ws.transport:
                            ch.ws.transport.close()
                        else:
                            await asyncio.wait_for(ch.ws.close(), timeout=0.5)
                    except Exception:
                        pass
                    ch.ws = None

            await self._server.wait_closed()
            self._server = None

        self.is_started = False
        ConsoleLogger.write("[WS] сервер остановлен", LogLevel.INFO)

    async def _handler(self, ws) -> None:
        """Обработчик событий"""
        remote = ws.remote_address
        channel = None
        try:
            channel = await self._handshake(ws, remote)
            if channel is None:
                await ws.close(1008, "bad hello")
                return
            await self._pump(ws, channel)
        except asyncio.CancelledError:
            ConsoleLogger.write(
                f"[WS] {remote}: handler cancelled", LogLevel.WARNING
            )
            raise
        except websockets.ConnectionClosed as e:
            ConsoleLogger.write(
                f"[WS] {remote}: connection closed: {e}", LogLevel.INFO
            )
        except Exception as e:
            ConsoleLogger.write(
                f"[WS] {remote}: {type(e).__name__}: {e}", LogLevel.ERROR
            )
        finally:
            if channel is not None:
                channel.detach()
                ConsoleLogger.write(
                    f"[WS] Канал {channel.slot_id} отключен. "
                    f"Ожидается повторное подключение...",
                    LogLevel.WARNING,
                )

    async def _handshake(self, ws, remote) -> Optional[WsChannel]:
        """ Рукопожатие. Возможность подключения только нужных клиентов (только платы на тесте)"""
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.hello_timeout)
        except asyncio.TimeoutError:
            ConsoleLogger.write(
                f"[WS] {remote}: нет hello за {self.hello_timeout}s",
                LogLevel.WARNING,
            )
            return None
        except websockets.ConnectionClosed:
            return None

        msg = decode(raw)
        if not msg or msg.get("type") != "hello":
            ConsoleLogger.write(
                f"[WS] {remote}: первый пакет не hello", LogLevel.WARNING
            )
            return None

        slot_id = msg.get("slot_id")
        channel = self._channels.get(slot_id)
        if channel is None:
            ConsoleLogger.write(
                f"[WS] {remote}: неизвестный slot_id={slot_id!r}", LogLevel.WARNING
            )
            return None

        await channel.attach_ws(ws)
        ConsoleLogger.write(
            f"[WS] {slot_id} подключён ({remote})", LogLevel.SUCCESS
        )
        return channel

    async def _pump(self, ws, channel: WsChannel) -> None:
        """Прослушивание канала"""
        try:
            async for raw in ws:
                msg = decode(raw)
                if msg is not None:
                    await channel.push(msg)
        except websockets.ConnectionClosed as e:
            ConsoleLogger.write(
                f"[WS] {channel.slot_id} обрыв: {e}", LogLevel.WARNING
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            ConsoleLogger.write(
                f"[WS] {channel.slot_id} pump error: {type(e).__name__}: {e}",
                LogLevel.ERROR,
            )
        finally:
            ConsoleLogger.write(
                f"[WS] {channel.slot_id} отключён", LogLevel.WARNING
            )