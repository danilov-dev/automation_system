"""
TcpChannel — транспорт поверх уже установленного TCP-соединения.

В отличие от SerialChannel, здесь канал НЕ открывает сеть сам.
TCP-сервер общий (TcpRouter), writer приходит извне через attach_writer().
Канал — это «логическая привязка» pc_id ↔ активное соединение.

Контракт Channel:
  - send()        — писать в текущий writer или ChannelDead
  - incoming()    — очередь сообщений от этого PC (переживает переподключения)
  - is_alive      — есть ли сейчас writer и не закрыт ли он
"""
import asyncio
from contextlib import suppress
from typing import AsyncIterator, Optional

from app.channels.base import ChannelDead
from app.protocol import encode
from app.utils.console_logger import ConsoleLogger, LogLevel


class TcpChannel:
    name = "tcp"

    def __init__(self, pc_id: str):
        self.pc_id = pc_id
        self._writer: Optional[asyncio.StreamWriter] = None
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._started = False
        self._stopped = False

    # ── lifecycle ─────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        return (
            self._started
            and not self._stopped
            and self._writer is not None
            and not self._writer.is_closing()
        )

    async def start(self) -> None:
        """Ничего не открывает: сервер общий, writer придёт через attach_writer."""
        self._started = True
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self._writer is not None:
            with suppress(Exception):
                self._writer.close()
            self._writer = None
        # разбудить incoming()
        await self._incoming.put(None)

    # ── send / incoming ───────────────────────────────────

    async def send(self, msg: dict) -> None:
        if not self.is_alive:
            raise ChannelDead(f"[{self.pc_id}] tcp not alive")
        try:
            self._writer.write(encode(msg))
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            self._writer = None
            raise ChannelDead(f"[{self.pc_id}] tcp write failed: {e}") from e

    async def incoming(self) -> AsyncIterator[dict]:
        while True:
            msg = await self._incoming.get()
            if msg is None:              # sentinel — канал закрыт
                return
            yield msg

    # ── внутреннее API для TcpRouter ──────────────────────

    async def attach_writer(self, writer: asyncio.StreamWriter) -> None:
        """
        Привязать нового клиента.
        Если был старый writer — закрыть его: PC мог переподключиться
        (например, после падения сервиса или смены сети).
        """
        if self._writer is not None and self._writer is not writer:
            with suppress(Exception):
                self._writer.close()
        self._writer = writer

    def detach(self) -> None:
        """Отвязать текущего клиента. incoming() при этом НЕ завершается —
        канал ждёт переподключения."""
        self._writer = None

    async def push(self, msg: dict) -> None:
        """Роутер кладёт сюда расшифрованные сообщения от клиента."""
        await self._incoming.put(msg)