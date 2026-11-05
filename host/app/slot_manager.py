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
from host.app.slots.state import SlotState

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

    def are_all_slots_powered(self) -> bool:
        turned_on_slot_count = len([slot for slot in self.registry.slots.values() if slot.power_control.is_on])
        return turned_on_slot_count == len(self.registry.slots)

    async def start_all(self):
        """Запустить роутер и все слоты."""
        if self.are_all_slots_powered():
            self._started = True
            return

        if not self.registry.router.is_started:
            await self.registry.router.start()

        for slot in self.registry.slots.values():
            if slot.state in (SlotState.IDLE, SlotState.OFFLINE):
                await slot.start()

        self._started = all(slot.power_control.is_on for slot in self.registry.slots.values())
        logger.info(f"[SlotManager] slots started: {len(self.registry.slots)}")

    async def stop_all(self):
        """Остановить все слоты и роутер."""
        for slot in self.registry.slots.values():
            try:
                await slot.stop()
            except Exception as e:
                logger.error(f"[SlotManager] stop slot {slot.slot_id}: {e}")

        await self.registry.router.stop()

        self._started = False
        logger.info("[SlotManager] Stopped")

    async def start_slot(self, slot_id: str) -> None:
        """Запустить конкретный слот. Подается питание, начинается отслеживание"""
        slot = self.registry.get(slot_id)
        if slot is None:
            raise KeyError(f"slot {slot_id!r} not found")
        await slot.start()

    async def stop_slot(self, slot_id: str) -> None:
        """Остановить конкретный слот. Отключает питание"""
        slot = self.registry.get(slot_id)
        if slot is None:
            raise KeyError(f"slot {slot_id!r} not found")
        await slot.stop()

    async def start_test(self, slots_id: list[str], tests: list[str]) -> None:
        """Запустить тест на выбранных слотах"""
        pass

    async def stop_test(self, slots_id: list[str]) -> None:
        """Остановить тест на выбранных слотах"""
        pass


    def get_slot(self, slot_id: str) -> Slot | None:
        """Получить слот по slot_id."""
        return self.registry.get(slot_id)

    def all_slots(self) -> dict[str, Slot]:
        """Получить все слоты."""
        return self.registry.slots
