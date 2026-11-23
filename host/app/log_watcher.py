"""Asynchronous serial log reader with ordered rule dispatch and reconnect."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import aioserial
import serial

from host.app.utils.ansi_striper import strip_ansi


LineCallback = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class _Rule:
    name: str
    patterns: tuple[str, ...]
    callback: LineCallback


class LogWatcher:
    """Read a serial console, retain recent lines and run matching rules in FIFO order."""

    def __init__(
        self,
        slot_id: str,
        port: str,
        baudrate: int = 115200,
        idle_timeout: float = 0.5,
        buffer_size: int = 500,
        callback_queue_size: int = 1024,
        reconnect_initial_delay: float = 0.5,
        reconnect_max_delay: float = 30.0,
        max_line_bytes: int = 64 * 1024,
    ) -> None:
        if idle_timeout < 0:
            raise ValueError("idle_timeout must be non-negative")
        if buffer_size < 1 or callback_queue_size < 1:
            raise ValueError("buffer and callback queue sizes must be positive")
        if reconnect_initial_delay <= 0 or reconnect_max_delay < reconnect_initial_delay:
            raise ValueError("invalid reconnect delay range")
        if max_line_bytes < 1:
            raise ValueError("max_line_bytes must be positive")

        self.slot_id = slot_id
        self.port = port
        self.baudrate = baudrate
        self.idle_timeout = idle_timeout
        self.callback_queue_size = callback_queue_size
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.max_line_bytes = max_line_bytes
        self.lines: deque[str] = deque(maxlen=buffer_size)
        self._rules: list[_Rule] = []
        self._ser: aioserial.AioSerial | None = None
        self._reader_task: asyncio.Task | None = None
        self._dispatcher_task: asyncio.Task | None = None
        self._callback_queue: asyncio.Queue[str] | None = None
        self._stop_event: asyncio.Event | None = None
        self._running = False
        self._alive = False
        self._write_lock = asyncio.Lock()
        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        return (
            self._running and self._alive
            and self._ser is not None and self._ser.is_open
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._alive = False
        self._stop_event = asyncio.Event()
        self._callback_queue = asyncio.Queue(maxsize=self.callback_queue_size)
        self._dispatcher_task = asyncio.create_task(
            self._dispatch_loop(), name=f"log-dispatch-{self.slot_id}"
        )
        self._reader_task = asyncio.create_task(
            self._connection_loop(), name=f"log-reader-{self.slot_id}"
        )
        await asyncio.sleep(0)
        self.logger.info("LogWatcher supervisor started for %s", self.port)

    async def stop(self) -> None:
        if (
            not self._running and self._reader_task is None
            and self._dispatcher_task is None and self._ser is None
        ):
            return
        self._running = False
        self._alive = False
        if self._stop_event is not None:
            self._stop_event.set()
        serial_port = self._ser
        self._ser = None
        if serial_port is not None:
            self._close_port(serial_port)
        tasks = [
            task for task in (self._reader_task, self._dispatcher_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reader_task = None
        self._dispatcher_task = None
        self._callback_queue = None
        self._stop_event = None
        self.logger.info("LogWatcher stopped for %s", self.port)

    def register_rule(
        self, patterns: list[str], callback: LineCallback, name: str = ""
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        normalized = tuple(pattern.lower() for pattern in patterns if pattern)
        if not normalized:
            raise ValueError("a rule must contain at least one non-empty pattern")
        rule_name = name or f"rule-{len(self._rules)}"
        self._rules.append(_Rule(rule_name, normalized, callback))
        self.logger.debug("Rule %s added", rule_name)

    async def send_line(self, text: str) -> bool:
        async with self._write_lock:
            serial_port = self._ser
            if not self.is_alive or serial_port is None:
                return False
            payload = (text.rstrip("\r\n") + "\n").encode("utf-8")
            try:
                await serial_port.write_async(payload)
            except (serial.SerialException, OSError) as exc:
                self.logger.warning("LogWatcher %s write failed: %s", self.port, exc)
                if self._ser is serial_port:
                    self._ser = None
                    self._alive = False
                self._close_port(serial_port)
                return False
        self.logger.debug("LogWatcher sent %d bytes to %s", len(payload), self.port)
        return True

    async def _connection_loop(self) -> None:
        delay = self.reconnect_initial_delay
        while self._running:
            if self._ser is None:
                try:
                    serial_port = aioserial.AioSerial(
                        port=self.port, baudrate=self.baudrate, timeout=0.1
                    )
                except (serial.SerialException, OSError) as exc:
                    self._alive = False
                    self.logger.warning(
                        "Cannot open %s: %s; retry in %.1fs", self.port, exc, delay
                    )
                    await self._wait_before_retry(delay)
                    delay = min(delay * 2, self.reconnect_max_delay)
                    continue
                self._ser = serial_port
                self._alive = True
                delay = self.reconnect_initial_delay
                self.logger.info("LogWatcher connected to %s@%s", self.port, self.baudrate)
                await self._check_auth()
                if self._ser is None:
                    await self._wait_before_retry(delay)
                    delay = min(delay * 2, self.reconnect_max_delay)
                    continue
            serial_port = self._ser
            if serial_port is None:
                continue
            try:
                await self._read_connection(serial_port)
            except asyncio.CancelledError:
                raise
            except (serial.SerialException, OSError) as exc:
                self.logger.warning("LogWatcher %s disconnected: %s", self.port, exc)
            finally:
                if self._ser is serial_port:
                    self._ser = None
                self._alive = False
                self._close_port(serial_port)
            if self._running:
                self.logger.info("Reconnecting to %s in %.1fs", self.port, delay)
                await self._wait_before_retry(delay)
                delay = min(delay * 2, self.reconnect_max_delay)

    async def _read_connection(self, serial_port: aioserial.AioSerial) -> None:
        buffer = bytearray()
        last_data_time = time.monotonic()
        while self._running and self._ser is serial_port and serial_port.is_open:
            waiting = serial_port.in_waiting
            if waiting:
                chunk = await serial_port.read_async(min(waiting, 4096))
                if chunk:
                    buffer.extend(chunk)
                    last_data_time = time.monotonic()
                    while True:
                        try:
                            newline = buffer.index(10)
                        except ValueError:
                            break
                        line = bytes(buffer[:newline])
                        del buffer[: newline + 1]
                        await self._enqueue_line(line)
                    while len(buffer) > self.max_line_bytes:
                        oversized = bytes(buffer[: self.max_line_bytes])
                        del buffer[: self.max_line_bytes]
                        self.logger.warning(
                            "Split oversized log line at %d bytes", self.max_line_bytes
                        )
                        await self._enqueue_line(oversized)
            else:
                if buffer and time.monotonic() - last_data_time > self.idle_timeout:
                    await self._enqueue_line(bytes(buffer))
                    buffer.clear()
                await asyncio.sleep(0.01)
        if buffer:
            await self._enqueue_line(bytes(buffer))

    async def _enqueue_line(self, raw: bytes) -> None:
        text = strip_ansi(raw.decode("utf-8", errors="backslashreplace"))
        text = text.rstrip("\r").strip()
        queue = self._callback_queue
        if text and self._running and queue is not None:
            await queue.put(text)

    async def _dispatch_loop(self) -> None:
        queue = self._callback_queue
        if queue is None:
            return
        while self._running:
            line = await queue.get()
            self.lines.append(line)
            lowered = line.lower()
            for rule in tuple(self._rules):
                if not any(pattern in lowered for pattern in rule.patterns):
                    continue
                try:
                    result = rule.callback(line)
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception("LogWatcher rule %r failed", rule.name)

    async def _wait_before_retry(self, delay: float) -> None:
        if self._stop_event is None:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _close_port(serial_port: aioserial.AioSerial) -> None:
        try:
            serial_port.close()
        except Exception:
            logging.getLogger(__name__).exception("Failed to close serial port")

    async def _check_auth(self) -> None:
        await self.send_line("\n")
