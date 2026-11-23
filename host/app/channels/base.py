"""Transport protocol and common transport errors."""
from typing import AsyncIterator, Protocol, runtime_checkable


class ChannelError(Exception):
    """Base error raised by a channel."""


class ChannelDead(ChannelError):
    """The channel is unavailable or failed while sending."""


@runtime_checkable
class Channel(Protocol):
    name: str

    @property
    def is_alive(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, msg: dict) -> None: ...

    def incoming(self) -> AsyncIterator[dict]: ...
