"""Serial transport for line-delimited JSON messages."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import AsyncIterator

import aioserial
import serial

from .base import ChannelDead
from .incoming import IncomingMessageQueue
from host.app.protocol import decode, encode


class SerialChannel:
    name = "serial"

    def __init__(
        self,
        slot_id: str,
        port: str,
        baudrate: int = 115200,
        idle_timeout: float = 1.0,
        incoming_queue_size: int = 1024,
    ) -> None:
        self.slot_id = slot_id
        self.port = port
        self.baudrate = baudrate
        self.idle_timeout = idle_timeout
        self._ser: aioserial.AioSerial | None = None
        self._alive = False
        self._started = False
        self._reader_task: asyncio.Task | None = None
        self.incoming_queue_size = incoming_queue_size
        self._incoming = IncomingMessageQueue(incoming_queue_size)
        self._write_lock = asyncio.Lock()
        self.logger = logging.getLogger(f"slot_{slot_id}")

    @property
    def is_alive(self) -> bool:
        return (
            self._started
            and self._alive
            and self._ser is not None
            and self._ser.is_open
        )

    async def start(self) -> None:
        if self._started and self.is_alive:
            return
        if self._reader_task is not None:
            await self.stop()
        self._incoming = IncomingMessageQueue(self.incoming_queue_size)
        try:
            serial_port = aioserial.AioSerial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=0.1,
            )
        except (serial.SerialException, OSError) as exc:
            self._started = False
            self._alive = False
            await self._incoming.close()
            raise ChannelDead(
                f"[{self.slot_id}] cannot open {self.port}: {exc}"
            ) from exc

        self._ser = serial_port
        self._alive = True
        self._started = True
        self._reader_task = asyncio.create_task(
            self._read_loop(serial_port, self._incoming), name=f"serial-reader-{self.slot_id}"
        )
        self.logger.info("Serial port %s opened", self.port)

    async def stop(self) -> None:
        if not self._started and self._ser is None and self._reader_task is None:
            return
        self._started = False
        self._alive = False
        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)

        serial_port = self._ser
        self._ser = None
        if serial_port is not None:
            with suppress(Exception):
                serial_port.close()
        await self._incoming.close()
        self.logger.info("Serial port %s closed", self.port)

    async def send(self, msg: dict) -> None:
        async with self._write_lock:
            serial_port = self._ser
            if not self.is_alive or serial_port is None:
                raise ChannelDead(f"[{self.slot_id}] serial channel is not alive")
            try:
                await serial_port.write_async(encode(msg))
            except (serial.SerialException, OSError) as exc:
                self._alive = False
                if self._ser is serial_port:
                    self._ser = None
                with suppress(Exception):
                    serial_port.close()
                raise ChannelDead(
                    f"[{self.slot_id}] serial write failed: {exc}"
                ) from exc

    async def incoming(self) -> AsyncIterator[dict]:
        async for message in self._incoming.iterate():
            yield message

    async def _read_loop(
        self,
        serial_port: aioserial.AioSerial,
        incoming: IncomingMessageQueue,
    ) -> None:
        buffer = bytearray()
        last_data_time = time.monotonic()
        try:
            while self._started and self._ser is serial_port and serial_port.is_open:
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
                            message = decode(line)
                            if message is not None:
                                await incoming.put(message)
                else:
                    if buffer and time.monotonic() - last_data_time > self.idle_timeout:
                        message = decode(bytes(buffer))
                        if message is not None:
                            await incoming.put(message)
                        buffer.clear()
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        except (serial.SerialException, OSError) as exc:
            self.logger.warning("[%s] serial read failed: %s", self.slot_id, exc)
        finally:
            if self._ser is serial_port:
                self._ser = None
                self._alive = False
                self._started = False
            with suppress(Exception):
                serial_port.close()
            await incoming.close()

    async def send_heartbeat(self) -> None:
        """Optional application-level heartbeat for serial-connected devices."""
        if self.is_alive:
            await self.send({"type": "heartbeat"})
