"""
SlotManager — тонкий оркестратор парка слотов.
Отвечает за:
— запуск/остановку роутера и всех слотов
— выполнение тестов на слотах (через slot.run_tests)
— трансляцию событий от слотов внешним клиентам (TUI, веб)
— трансляцию результатов тестов
НЕ отвечает за:
— выполнение тестов внутри слота (это SlotTestRunner)
— маршрутизацию задач (это TaskRouter)
— управление состоянием слота (это SlotStateController)
"""
import asyncio
import logging
from typing import Awaitable, Callable, Union
from host.app.slots.slot_fabric import SlotFabric
from host.app.slots.slot import Slot
from host.app.slots.state import SlotState
from host.app.testing.test_result import TestResult

logger = logging.getLogger(__name__)

# Колбэк о смене состояния слота: (slot_id, old_state, new_state)
StateCallback = Callable[[str, str, str], Union[None, Awaitable[None]]]
# Колбэк о результате теста: (result)
TestResultCallback = Callable[[TestResult], Union[None, Awaitable[None]]]


class SlotManager:
    def __init__(self, registry: SlotFabric):
        self.registry = registry
        self._started = False
        self._state_listeners: list[StateCallback] = []
        self._test_result_listeners: list[TestResultCallback] = []

        for slot in registry.slots.values():
            slot.on_state_change(self._on_slot_state)
            slot.on_test_result(self._on_slot_test_result)

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

    async def _on_slot_test_result(self, result: TestResult) -> None:
        """Вызывается слотом после завершения теста. Транслирует внешним клиентам."""
        for cb in self._test_result_listeners:
            try:
                res = cb(result)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"[SlotManager] test_result cb error: {e}")

    def on_test_result(self, cb: TestResultCallback) -> None:
        """Подписка UI на завершение теста."""
        self._test_result_listeners.append(cb)

    # ── Публичное API: тесты ────────────────────────────
    async def run_tests(
            self,
            slot_ids: list[str],
            tests: list[str],
            parallel: bool = True,
    ) -> dict[str, list[TestResult]]:
        """
        Запустить тесты на указанных слотах.
        Слоты работают параллельно, тесты внутри слота — последовательно.
        """
        await self.start_router()

        async def _one(sid: str):
            slot = self.registry.get(sid)
            if slot is None:
                return sid, []
            if slot.state in (SlotState.IDLE, SlotState.OFFLINE):
                await slot.start()
            return sid, await slot.run_tests(tests)

        if parallel:
            pairs = await asyncio.gather(*(_one(s) for s in slot_ids))
        else:
            pairs = [await _one(s) for s in slot_ids]
        return dict(pairs)

    def stop_tests(self, slot_id: str | None = None) -> None:
        """Отменить тесты на слоте или всех слотах."""
        targets = [self.registry.get(slot_id)] if slot_id else self.registry.slots.values()
        for slot in targets:
            if slot:
                slot.cancel_tests()

    # ── Lifecycle парка ─────────────────────────────────
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