"""
LogWatcher — чтение COM1 (консоль ядра/BIOS), детект паттернов, колбэки.

Принципиально НЕ Channel:
  - нет JSON-протокола (COM1 — сырая консоль);
  - нет task_id, нет send(msg: dict);
  - много подписчиков (правил), а не один поток incoming().

send_line(text) — единственная запись; нужен для автологина.
"""
import asyncio
import time
from collections import deque
from contextlib import suppress
from typing import Awaitable, Callable, Optional, Union

import aioserial
import serial

from app.utils.console_logger import ConsoleLogger, LogLevel, strip_ansi


LineCallback = Callable[[str], Union[None, Awaitable[None]]]


class LogWatcher:
    def __init__(
        self,
        pc_id: str,
        port: str,
        baudrate: int = 115200,
        idle_timeout: float = 0.5,
        buffer_size: int = 500,
    ):
        self.pc_id = pc_id
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

    # ── lifecycle ─────────────────────────────────────────

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
                port=self.port, baudrate=self.baudrate, timeout=0.1
            )
        except serial.SerialException as e:
            raise RuntimeError(
                f"[{self.pc_id}] не открыть COM1 {self.port}: {e}"
            ) from e
        self._alive = True
        self._reader_task = asyncio.create_task(self._read_loop())
        ConsoleLogger.write(
            f"[{self.pc_id}] LogWatcher {self.port} открыт @ {self.baudrate}",
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
        ConsoleLogger.write(
            f"[{self.pc_id}] LogWatcher {self.port} закрыт", LogLevel.INFO
        )

    # ── rules ─────────────────────────────────────────────

    def on(
        self,
        patterns: list[str],
        callback: LineCallback,
        name: str = "",
    ) -> None:
        """Зарегистрировать правило. Регистр не важен (сравнение по .lower())."""
        low = [p.lower() for p in patterns]
        rule_name = name or f"rule-{len(self._rules)}"
        self._rules.append((rule_name, low, callback))

    # ── write (только для авторизации) ────────────────────

    async def send_line(self, text: str) -> None:
        """Отправить сырую строку в COM1. Не JSON — консоль."""
        if not self.is_alive:
            return
        async with self._write_lock:
            try:
                await self._ser.write_async(
                    (text.rstrip("\r\n") + "\n").encode("utf-8")
                )
            except (serial.SerialException, OSError) as e:
                self._alive = False
                ConsoleLogger.write(
                    f"[{self.pc_id}] COM1 write error: {e}", LogLevel.ERROR
                )

    # ── read loop ─────────────────────────────────────────

    async def _read_loop(self) -> None:
        buffer = b""
        last_data_time = time.monotonic()
        try:
            while self._alive:
                try:
                    waiting = self._ser.in_waiting
                except (serial.SerialException, OSError) as e:
                    ConsoleLogger.write(
                        f"[{self.pc_id}] COM1 потерян: {e}", LogLevel.ERROR
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

    # ── dispatch ──────────────────────────────────────────

    def _dispatch(self, raw: bytes) -> None:
        """
        Превратить bytes в чистую строку и раздать правилам.

        Колбэки запускаются как отдельные задачи (create_task), чтобы
        медленный колбэк (запись в COM1, POST в API) не тормозил чтение.
        Порядок вызовов при этом не гарантирован — для мониторинга
        это приемлемо.
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
                    ConsoleLogger.write(
                        f"[{self.pc_id}] rule '{name}' sync err: {e}",
                        LogLevel.ERROR,
                    )

    async def _safe_call(self, name: str, cb: LineCallback, text: str) -> None:
        try:
            await cb(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.pc_id}] rule '{name}' async err: {e}", LogLevel.ERROR
            )