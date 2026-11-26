"""
Channel — абстракция транспорта (web socket или Serial).

"""
from typing import Protocol, AsyncIterator, runtime_checkable


class ChannelError(Exception):
    """Базовая ошибка канала."""
    pass


class ChannelDead(ChannelError):
    """Канал недоступен: порт закрыт, соединение сброшено, writer отсутствует."""
    pass


@runtime_checkable
class Channel(Protocol):
    """Контракт транспорта."""
    name: str

    @property
    def is_alive(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, msg: dict) -> None: ...

    def incoming(self) -> AsyncIterator[dict]: ...
