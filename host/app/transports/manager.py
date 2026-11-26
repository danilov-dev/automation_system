import logging
from enum import Enum
from typing import Optional, Callable


class TransportState(Enum):
    AVAILABLE = "available"
    TESTING = "testing"
    DEAD = "dead"

logger = logging.getLogger(__name__)

class TransportManager:
    def __init__(self, slot_id: str) -> None:
        self.slot_id = slot_id
        self._states: dict[str, TransportState] = {}
        self._primary_transport: Optional[str] = None
        self._callbacks: list[Callable] = []

    def register_transport(self, transport_name: str, is_primary: bool = False) -> None:
        """
        Регистрация транспорта.
        Args:
            transport_name (): Название транспорта
            is_primary (): Является ли он основным.

        Returns: None
        """
        self._states[transport_name] = TransportState.AVAILABLE
        if is_primary:
            self._primary_transport = transport_name

    def mark_testing(self, transport_name: str) -> None:
        """
        Помечает транспорт как занятый тестированием
        Args:
            transport_name (): Название транспорта

        Returns: None
        """
        old_state = self._states.get(transport_name)
        self._states[transport_name] = TransportState.TESTING
        self._notify(transport_name, old_state, TransportState.TESTING)

    def mark_available(self, transport_name: str) -> None:
        """
        Помечает транспорт как занятый доступный
        Args:
            transport_name (): Название транспорта

        Returns: None
        """
        old_state = self._states.get(transport_name)
        self._states[transport_name] = TransportState.AVAILABLE
        self._notify(transport_name, old_state, TransportState.AVAILABLE)

    def mark_dead(self, transport_name: str) -> None:
        """
        Помечает транспорт как мертвый.
        Args:
            transport_name (): Название транспорта

        Returns: None
        """
        old_state = self._states.get(transport_name)
        self._states[transport_name] = TransportState.DEAD
        self._notify(transport_name, old_state, TransportState.DEAD)

    def get_available_transport(self) -> Optional[str]:
        """
        Возвращает доступный транспорт

        Returns: Optional[str]
        """
        if self._primary_transport:
            state = self._states.get(self._primary_transport)
            if state == TransportState.AVAILABLE:
                return self._primary_transport

        for name, state in self._states.items():
            if state is TransportState.AVAILABLE:
                return name

        return None

    def is_available(self, transport_name: str) -> bool:
        """
        Проверяет состояние транспорта
        Args:
            transport_name (): Название транспорта

        Returns: bool

        """
        return self._states.get(transport_name) == TransportState.AVAILABLE

    def on_state_change(self, callback: Callable) -> None:
        """
        Подписка на изменения состояния
        Args:
            callback (): Обработчик события

        Returns: None

        """
        self._callbacks.append(callback)

    def _notify(self, transport_name: str, old_state: TransportState, new_state: TransportState) -> None:
        """
        Оповещение подписчиков о событии
        Args:
            transport_name (): Название транспорта
            old_state (): Предыдущее состояние
            new_state (): Новое состояние

        Returns: None

        """
        for callback in self._callbacks:
            try:
                callback(self.slot_id, transport_name, old_state, new_state)
            except Exception as e:
                logger.error(f"Failed to notify {transport_name}: {e}")