"""Bounded, restartable queue for a single incoming-message consumer."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

_STOP = object()

class IncomingMessageQueue:
    """Bounded FIFO queue with a reserved slot for a shutdown sentinel."""
    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize + 1)
        self._slots = asyncio.Semaphore(maxsize)
        self._closed = False

    def reset(self) -> None:
        """Start a fresh queue generation after channel restart."""
        self._queue = asyncio.Queue(maxsize=self._maxsize + 1)
        self._slots = asyncio.Semaphore(self._maxsize)
        self._closed = False

    async def put(self, message: dict) -> bool:
        if self._closed:
            return False
        await self._slots.acquire()
        if self._closed:
            self._slots.release()
            return False
        await self._queue.put(message)
        return True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # The semaphore reserves maxsize slots for messages; this extra slot
        # is always available for the shutdown sentinel.
        await self._queue.put(_STOP)

    async def iterate(self) -> AsyncIterator[dict]:
        queue = self._queue
        slots = self._slots
        while True:
            item = await queue.get()
            if item is _STOP:
                return
            slots.release()
            yield item
