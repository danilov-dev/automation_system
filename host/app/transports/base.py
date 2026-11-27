"""
Transport — абстракция транспорта (WebSocket или Serial).
"""
from typing import Protocol, AsyncIterator, runtime_checkable


class TransportError(Exception):
    """Базовая ошибка транспорта."""
    pass


class TransportDead(TransportError):
    """Транспорт недоступен: порт закрыт, соединение сброшено, writer отсутствует."""
    pass


@runtime_checkable
class Transport(Protocol):
    """Контракт транспорта."""
    name: str
    
    @property
    def is_alive(self) -> bool:
        """Физическое состояние: порт открыт, соединение установлено."""
        ...
    
    @property
    def is_available(self) -> bool:
        """Логическое состояние: можно ли использовать как транспорт прямо сейчас."""
        ...
    
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, msg: dict) -> None: ...
    def incoming(self) -> AsyncIterator[dict]: ...
    
    def mark_for_testing(self) -> None:
        """Пометить транспорт как занятый тестированием."""
        ...
    
    def release_from_testing(self) -> None:
        """Освободить транспорт после тестирования."""
        ...