"""
LogWatcher — чтение COM1 (консоль ядра/BIOS), выявление паттернов, колбэки.
"""
import asyncio
import time
import logging
from collections import deque
from contextlib import suppress
from typing import Awaitable, Callable, Optional, Union

import aioserial
import serial

from host.app.utils.ansi_striper import strip_ansi

LineCallback = Callable[[str], Union[None, Awaitable[None]]]


class LogWatcher:
    def __init__(
            self,
            slot_id: str,
            port: str,
            baudrate: int = 115200,
            idle_timeout: float = 0.5,
            buffer_size: int = 500,
    ):
        self.slot_id = slot_id
        self.port = port
        self.baudrate = baudrate
        self.idle_timeout = idle_timeout

        # последние N строк — для отладки и выгрузки в API
        self.lines: deque[str] = deque(maxlen=buffer_size)
        # (name, [patterns_lower], callback)
        self._rules: list[tuple[str, list[str], LineCallback]] = []
        # активные задачи-колбэки — чтобы аккуратно их отменять в stop()
        self._tasks: set[asyncio.Task] = set()
        self._ser: Optional[aioserial.AioSerial] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._alive = False
        self._write_lock = asyncio.Lock()

        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        return (
                self._alive
                and self._ser is not None
                and self._ser.is_open
        )

    async def start(self) -> None:
        """Открытие порта"""
        if self._alive:
            return
        try:
            self._ser = aioserial.AioSerial(
                port=self.port, baudrate=self.baudrate, timeout=0.1
            )
        except serial.SerialException as e:
            raise RuntimeError(
                f"[{self.slot_id}] не открыть COM1 {self.port}: {e}"
            ) from e

        self._alive = True
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._check_auth()
        self.logger.info(f"LogWatcher opened {self.port}@ {self.baudrate}")

    async def stop(self) -> None:
        """Завершение работы и закрытие порта"""
        if not self._alive and self._ser is None:
            return
        self._alive = False
        if self._reader_task:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        # отменяем «висящие» колбэки
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            with suppress(Exception):
                await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        if self._ser:
            with suppress(Exception):
                self._ser.close()
            self._ser = None
        self.logger.info(f"LogWatcher {self.port} closed")

    def register_rule(
            self,
            patterns: list[str],
            callback: LineCallback,
            name: str = "",
    ) -> None:
        """Зарегистрировать правило. Регистр не важен (сравнение по .lower())."""
        low = [p.lower() for p in patterns]
        rule_name = name or f"rule-{len(self._rules)}"
        self._rules.append((rule_name, low, callback))

        self.logger.debug(f"Rule {rule_name} added")

    async def send_line(self, text: str) -> None:
        """Отправить сырую строку."""
        if not self.is_alive:
            return
        async with self._write_lock:
            try:
                await self._ser.write_async(
                    (text.rstrip("\r\n") + "\n").encode("utf-8")
                )
                self.logger.debug(f"LogWatcher send {text} to {self.port}")
            except (serial.SerialException, OSError) as e:
                self._alive = False
                self.logger.error(f"LogWatcher {self.port} write error: {e}")

    async def _read_loop(self) -> None:
        """Цикл чтение из порта"""
        buffer = b""
        last_data_time = time.monotonic()
        try:
            while self._alive:
                try:
                    waiting = self._ser.in_waiting
                except (serial.SerialException, OSError) as e:
                    self.logger.error(f"LogWatcher {self.port} was lost: {e}")
                    self._alive = False
                    break

                if waiting:
                    chunk = await self._ser.read_async(waiting)
                    if chunk:
                        buffer += chunk
                        last_data_time = time.monotonic()
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            self._dispatch(line)
                else:
                    if (
                            buffer
                            and (time.monotonic() - last_data_time)
                            > self.idle_timeout
                    ):
                        self._dispatch(buffer)
                        buffer = b""
                    await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            raise

    def _dispatch(self, raw: bytes) -> None:
        """
        Превратить bytes в чистую строку и раздать правилам.
        """
        text = strip_ansi(
            raw.decode("utf-8", errors="backslashreplace")
        ).rstrip("\r").strip()
        if not text:
            return

        self.lines.append(text)
        low = text.lower()
        for name, patterns, cb in self._rules:
            if not any(p in low for p in patterns):
                continue
            if asyncio.iscoroutinefunction(cb):
                t = asyncio.create_task(self._safe_call(name, cb, text))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)
            else:
                try:
                    cb(text)
                except Exception as e:
                    self.logger.error(f"rule '{name}' sync err: {e}", self.slot_id, name, e)

    async def _safe_call(self, name: str, cb: LineCallback, text: str) -> None:
        try:
            await cb(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"rule '{name}' async err: {e}")

    async def _check_auth(self):
        """Проверка авторизации"""
        self.logger.debug(f"LogWatcher {self.port} check auth")
        await self.send_line('\n')
