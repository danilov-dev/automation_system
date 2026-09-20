"""
SerialChannel — транспорт поверх COM-порта.

Протокол: line-delimited JSON (см. app/protocol.py).
Ничего не знает про команды/отчёты — только dict in / dict out.

Особенности реализации:
  - Чтение через in_waiting + read_async: не блокируемся, если PC молчит.
  - Буферизация до '\n': если пришёл обрывок пакета — ждём остаток.
  - idle_timeout: если строка пришла без '\\n' и порт молчит N секунд —
    считаем её целым сообщением (страховка от нестандартных клиентов).
  - incoming() завершается (не висит) при stop() или ошибке порта.
"""
import asyncio
import time
from contextlib import suppress
from typing import AsyncIterator, Optional

import aioserial
import serial

from app.channels.base import ChannelDead
from app.protocol import encode, decode
from app.utils.console_logger import ConsoleLogger, LogLevel


class SerialChannel:
    name = "serial"

    def __init__(
        self,
        pc_id: str,
        port: str,
        baudrate: int = 115200,
        idle_timeout: float = 1.0,
    ):
        self.pc_id = pc_id
        self.port = port
        self.baudrate = baudrate
        self.idle_timeout = idle_timeout

        self._ser: Optional[aioserial.AioSerial] = None
        self._alive = False
        self._reader_task: Optional[asyncio.Task] = None
        self._incoming: asyncio.Queue = asyncio.Queue()

    # ── lifecycle ──────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        return (
            self._alive
            and self._ser is not None
            and self._ser.is_open
        )

    async def start(self) -> None:
        if self._alive:
            return
        try:
            self._ser = aioserial.AioSerial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=0.1,
            )
        except serial.SerialException as e:
            raise ChannelDead(
                f"[{self.pc_id}] не открыть {self.port}: {e}"
            ) from e

        self._alive = True
        self._reader_task = asyncio.create_task(self._read_loop())
        ConsoleLogger.write(
            f"[{self.pc_id}] Serial {self.port} открыт @ {self.baudrate}",
            LogLevel.INFO,
        )

    async def stop(self) -> None:
        if not self._alive and self._ser is None:
            return
        self._alive = False
        if self._reader_task:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        if self._ser:
            with suppress(Exception):
                self._ser.close()
            self._ser = None
        # разбудить incoming()
        await self._incoming.put(None)
        ConsoleLogger.write(
            f"[{self.pc_id}] Serial {self.port} закрыт", LogLevel.INFO
        )

    # ── send ───────────────────────────────────────────────

    async def send(self, msg: dict) -> None:
        if not self.is_alive:
            raise ChannelDead(f"[{self.pc_id}] serial not alive")
        try:
            await self._ser.write_async(encode(msg))
        except (serial.SerialException, OSError) as e:
            self._alive = False
            raise ChannelDead(
                f"[{self.pc_id}] write failed: {e}"
            ) from e

    # ── incoming ───────────────────────────────────────────

    async def incoming(self) -> AsyncIterator[dict]:
        while True:
            msg = await self._incoming.get()
            if msg is None:          # sentinel — канал закрыт
                return
            yield msg

    # ── reader ─────────────────────────────────────────────

    async def _read_loop(self) -> None:
        buffer = b""
        last_data_time = time.monotonic()
        try:
            while self._alive:
                try:
                    waiting = self._ser.in_waiting
                except (serial.SerialException, OSError) as e:
                    ConsoleLogger.write(
                        f"[{self.pc_id}] Serial {self.port} потерян: {e}",
                        LogLevel.ERROR,
                    )
                    self._alive = False
                    break

                if waiting:
                    chunk = await self._ser.read_async(waiting)
                    if chunk:
                        buffer += chunk
                        last_data_time = time.monotonic()
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            msg = decode(line)
                            if msg:
                                await self._incoming.put(msg)
                else:
                    # порт молчит, но есть неполная строка — сбросим по idle
                    if (
                        buffer
                        and (time.monotonic() - last_data_time)
                        > self.idle_timeout
                    ):
                        msg = decode(buffer)
                        if msg:
                            await self._incoming.put(msg)
                        buffer = b""
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        finally:
            # если вышли из-за ошибки (не из-за stop()) — разбудить incoming()
            if not self._alive:
                with suppress(Exception):
                    self._incoming.put_nowait(None)