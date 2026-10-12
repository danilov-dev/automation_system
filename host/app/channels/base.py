"""
Channel — абстракция транспорта (TCP или Serial).

Задача Channel: уметь отправить dict и отдать поток входящих dict.
Channel НЕ знает:
  - что такое команда, отчёт, task_id;
  - кто такой PcSession;
  - что делать при ошибке.

Этим занимается слой выше (PcSession / Orchestrator).
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
    """
    Контракт транспорта.

    name        — "tcp" | "serial", для логов.
    is_alive    — можно ли прямо сейчас пытаться send().
    start/stop  — жизненный цикл (открыть/закрыть порт, поднять reader).
    send(msg)   — положить dict в канал. При разрыве — ChannelDead.
    incoming()  — асинхронный поток входящих dict. Гарантия: если канал
                  закрылся, генератор завершается, а не висит.
    """
    name: str

    @property
    def is_alive(self) -> bool: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, msg: dict) -> None: ...
    def incoming(self) -> AsyncIterator[dict]: ...