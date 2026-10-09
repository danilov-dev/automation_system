"""
TcpRouter — единый TCP-сервер для всех слотов.

Логика:
  1. Принимает TCP-подключение.
  2. Ждёт первый пакет {"type":"hello","pc_id":"slotN"} с таймаутом.
  3. Ищет TcpChannel по pc_id в реестре.
  4. attach_writer(channel, writer) → дальше все строки → channel.push().
  5. При обрыве — channel.detach(), writer закрыт.
"""
import asyncio
from contextlib import suppress
from typing import Dict, Optional

from app.channels.tcp import TcpChannel
from app.protocol import decode
from app.utils.console_logger import ConsoleLogger, LogLevel


class TcpRouter:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        hello_timeout: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.hello_timeout = hello_timeout

        self._channels: Dict[str, TcpChannel] = {}
        self._server: Optional[asyncio.Server] = None

        self._heartbeat_task = None
        self._heartbeat_interval = 30.0
        self.is_started = False

    # ── реестр каналов ────────────────────────────────────

    def register(self, channel: TcpChannel) -> None:
        if channel.pc_id in self._channels:
            raise ValueError(f"pc_id '{channel.pc_id}' уже зарегистрирован")
        self._channels[channel.pc_id] = channel

    # ── lifecycle ─────────────────────────────────────────

    @property
    def actual_port(self) -> int:
        """Фактический порт (полезно в тестах с port=0)."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self.port

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handler, self.host, self.port
        )

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        self.is_started = True

        ConsoleLogger.write(
            f"[TCP] сервер слушает {self.host}:{self.actual_port}",
            LogLevel.SUCCESS,
        )

    async def stop(self) -> None:
        if self._server:

            if self._heartbeat_task:
                self._heartbeat_task.cancel()

            self._server.close()
            with suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        # закрыть всех клиентов, чтобы reader-loop'ы завершились
        for ch in self._channels.values():
            if ch._writer is not None:
                with suppress(Exception):
                    ch._writer.close()
                ch._writer = None
        self.is_started = False

        ConsoleLogger.write("[TCP] сервер остановлен", LogLevel.INFO)

    # ── handler ───────────────────────────────────────────

    async def _handler(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peername = writer.get_extra_info("peername")
        try:
            channel = await self._handshake(reader, writer, peername)
            if channel is None:
                return
            await self._pump(reader, channel)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            ConsoleLogger.write(
                f"[TCP] {peername}: {type(e).__name__}: {e}",
                LogLevel.ERROR,
            )
        finally:
            # снять writer и закрыть соединение
            for ch in self._channels.values():
                if ch._writer is writer:
                    ch.detach()
                    break
            with suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peername,
    ) -> Optional[TcpChannel]:
        try:
            line = await asyncio.wait_for(
                reader.readline(), timeout=self.hello_timeout
            )
        except asyncio.TimeoutError:
            ConsoleLogger.write(
                f"[TCP] {peername}: нет hello за {self.hello_timeout}s",
                LogLevel.WARNING,
            )
            return None

        if not line:
            return None

        msg = decode(line)
        if not msg or msg.get("type") != "hello":
            ConsoleLogger.write(
                f"[TCP] {peername}: первый пакет не hello",
                LogLevel.WARNING,
            )
            return None

        pc_id = msg.get("pc_id")
        channel = self._channels.get(pc_id)
        if channel is None:
            ConsoleLogger.write(
                f"[TCP] {peername}: неизвестный pc_id={pc_id!r}",
                LogLevel.WARNING,
            )
            return None

        await channel.attach_writer(writer)
        ConsoleLogger.write(
            f"[TCP] {pc_id} подключён ({peername})",
            LogLevel.SUCCESS,
        )
        return channel

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        channel: TcpChannel,
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = decode(line)
                if msg is not None:
                    await channel.push(msg)
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            ConsoleLogger.write(
                f"[TCP] {channel.pc_id} обрыв: {e}", LogLevel.WARNING
            )
        finally:
            ConsoleLogger.write(
                f"[TCP] {channel.pc_id} отключён", LogLevel.WARNING
            )

    async def _heartbeat_loop(self):
        """Периодически отправляет heartbeat всем подключенным клиентам."""
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                for channel in self._channels.values():
                    if channel.is_alive:
                        await channel.send_heartbeat()
        except asyncio.CancelledError:
            pass