"""
WsRouter — единый WebSocket-сервер для всех слотов.
Логика:
  1. Принимает WS-подключение.
  2. Ждёт первое сообщение {"type":"hello","pc_id":"slotN"} с таймаутом.
  3. Ищет WsChannel по pc_id в реестре.
  4. attach_ws(channel, ws) → дальше все сообщения → channel.push().
  5. При обрыве — channel.detach(), ws закрыт.
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
    ):
        super().__init__(host=host, port=port, hello_timeout=hello_timeout)
        self._channels: Dict[str, WsChannel] = {}
        self._server: Optional[Server] = None
        self.ping_timeout = 10.0
        self.ping_interval = 10.0

    # ── реестр каналов ────────────────────────────────────
    def register(self, channel: WsChannel) -> None:
        if channel.pc_id in self._channels:
            raise ValueError(f"pc_id '{channel.pc_id}' уже зарегистрирован")
        self._channels[channel.pc_id] = channel

    # ── lifecycle ─────────────────────────────────────────
    @property
    def actual_port(self) -> int:
        if self._server is not None:
            # websockets >= 13: server.sockets — список сокетов
            sockets = getattr(self._server, "sockets", None) or []
            if sockets:
                return sockets[0].getsockname()[1]
        return self.port

    async def start(self) -> None:
        self._server = await serve(
            self._handler,
            self.host,
            self.port,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        )
        self.is_started = True
        ConsoleLogger.write(f"[WS] сервер слушает ws://{self.host}:{self.actual_port}", LogLevel.SUCCESS, )

    async def stop(self) -> None:
        if self._server:
            # 1. Запрещаем принимать новые подключения
            self._server.close()

            # ✅ ИСПРАВЛЕНИЕ 2: Принудительно рвем все активные TCP-соединения
            # Это нужно, чтобы хендлеры не висли в ожидании данных от "мертвых" клиентов
            for ch in self._channels.values():
                if ch.ws is not None:
                    try:
                        # Закрываем именно asyncio transport, чтобы не ждать ACK от отключенного ПК
                        if hasattr(ch.ws, 'transport') and ch.ws.transport:
                            ch.ws.transport.close()
                        else:
                            # Фоллбэк, если транспорт недоступен
                            await asyncio.wait_for(ch.ws.close(), timeout=0.5)
                    except Exception:
                        pass
                    ch.ws = None

            # 2. Теперь ждем завершения хендлеров (они упадут с ошибкой из-за закрытого транспорта)
            await self._server.wait_closed()
            self._server = None

        self.is_started = False
        ConsoleLogger.write("[WS] сервер остановлен", LogLevel.INFO)

    # ── handler ───────────────────────────────────────────
    async def _handler(self, ws) -> None:
        remote = ws.remote_address
        channel = None
        try:
            channel = await self._handshake(ws, remote)
            if channel is None:
                await ws.close(1008, "bad hello")
                return
            await self._pump(ws, channel)
        except websockets.ConnectionClosed as e:
            ConsoleLogger.write(f"[WS] {remote}: connection closed: {e}", LogLevel.INFO, )
        except Exception as e:
            ConsoleLogger.write(f"[WS] {remote}: {type(e).__name__}: {e}", LogLevel.ERROR, )
        finally:
            if channel is not None:
                channel.detach()
                ConsoleLogger.write(
                    f"[WS] Канал {channel.pc_id} переведен в неактивное состояние.is_alive={channel.is_alive}",
                    LogLevel.WARNING)
                with suppress(Exception):
                    await ws.close()

    async def _handshake(self, ws, remote) -> Optional[WsChannel]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.hello_timeout)
        except asyncio.TimeoutError:
            ConsoleLogger.write(f"[WS] {remote}: нет hello за {self.hello_timeout}s", LogLevel.WARNING, )
            return None
        except websockets.ConnectionClosed:
            return None

        msg = decode(raw)
        if not msg or msg.get("type") != "hello":
            ConsoleLogger.write(f"[WS] {remote}: первый пакет не hello", LogLevel.WARNING, )
            return None

        pc_id = msg.get("pc_id")
        channel = self._channels.get(pc_id)
        if channel is None:
            ConsoleLogger.write(f"[WS] {remote}: неизвестный pc_id={pc_id!r}", LogLevel.WARNING, )
            return None

        await channel.attach_ws(ws)
        ConsoleLogger.write(f"[WS] {pc_id} подключён ({remote})", LogLevel.SUCCESS, )
        return channel

    async def _pump(self, ws, channel: WsChannel) -> None:
        try:
            async for raw in ws:
                msg = decode(raw)
                if msg is not None:
                    await channel.push(msg)
        except websockets.ConnectionClosed as e:
            ConsoleLogger.write(
                f"[WS] {channel.pc_id} обрыв: {e}", LogLevel.WARNING
            )
        finally:
            ConsoleLogger.write(
                f"[WS] {channel.pc_id} отключён", LogLevel.WARNING
            )
