"""
SlotManager — тонкий оркестратор парка слотов.
Отвечает за:
  - запуск/остановку роутера и всех слотов
  - трансляцию событий от слотов внешним клиентам (TUI, веб)
"""
import asyncio
import logging
from typing import Awaitable, Callable, Union

from host.app.slots.slot_fabric import SlotFabric
from host.app.slots.slot import Slot

logger = logging.getLogger(__name__)

# Колбэк о смене состояния слота: (slot_id, state_name)
StateCallback = Callable[[str, str, str], Union[None, Awaitable[None]]]


class SlotManager:
    def __init__(self, registry: SlotFabric):
        self.registry = registry
        self._started = False
        self._state_listeners: list[StateCallback] = []

        for slot in registry.slots.values():
            slot.on_state_change(self._on_slot_state)

    async def _on_slot_state(self, slot_id: str, old_state: str, new_state: str):
        """Вызывается при смене состояния слота. Транслирует внешним клиентам."""
        for cb in self._state_listeners:
            try:
                res = cb(slot_id, old_state, new_state)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"[SlotManager] state cb error: {e}")

    def on_state_change(self, cb: StateCallback) -> None:
        """Подписка для внешних клиентов."""
        self._state_listeners.append(cb)

    @property
    def is_started(self) -> bool:
        return self._started

    async def start_router(self):
        """Запустить роутер"""
        if not self.registry.router.is_started:
            try:
                await self.registry.router.start()
            except Exception as e:
                logger.error(f"[SlotManager] start error: {e}")

    async def start_all(self):
        """Запустить роутер и все слоты."""
        if self._started:
            return
        if not self.registry.router.is_started:
            await self.registry.router.start()
        for slot in self.registry.slots.values():
            await slot.start()
        self._started = True
        logger.info(f"[SlotManager] slots was started: {len(self.registry.slots)}")

    async def stop_all(self):
        """Остановить все слоты и роутер."""
        if not self._started:
            return
        for slot in self.registry.slots.values():
            try:
                await slot.stop()
            except Exception as e:
                logger.error(f"[SlotManager] stop slot {slot.slot_id}: {e}")
        await self.registry.router.stop()
        self._started = False
        logger.info("[SlotManager] Stopped")

    async def start_slot(self, slot_id: str) -> None:
        """Запустить конкретный слот."""
        slot = self.registry.get(slot_id)
        if slot is None:
            raise KeyError(f"slot {slot_id!r} not found")
        await slot.start()

    async def stop_slot(self, slot_id: str) -> None:
        """Остановить конкретный слот."""
        slot = self.registry.get(slot_id)
        if slot is None:
            raise KeyError(f"slot {slot_id!r} not found")
        await slot.stop()

    def get_slot(self, slot_id: str) -> Slot | None:
        """Получить слот по slot_id."""
        return self.registry.get(slot_id)

    def all_slots(self) -> dict[str, Slot]:
        """Получить все слоты."""
        return self.registry.slots
