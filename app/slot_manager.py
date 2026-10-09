"""
SlotManager — оркестратор парка слотов.
Ответственности:
  - start_all / stop_all: поднять/погасить router + все слоты
  - start_slot / stop_slot: управление отдельным слотом
  - Подписка на события COM1 (reboot/shutdown/power_on)
  - Уведомление внешних подписчиков об изменении состояния слота

НЕ отвечает за:
  - Выполнение задач (это делает Slot.execute())
  - Маршрутизацию и failover (это внутри Slot)
"""
import asyncio
from typing import Awaitable, Callable, Union

from app.registry import Registry
from app.slot import Slot, SlotState
from app.utils.console_logger import ConsoleLogger, LogLevel


# Колбэк о смене состояния слота: (pc_id, state_name)
StateCallback = Callable[[str, str], Union[None, Awaitable[None]]]

# Паттерны для COM1
_REBOOT_PATTERNS = [
    "reboot: restarting system ",
    "systemd-reboot.service ",
    "reached target reboot.target ",
    "the system is going down for reboot ",
]
_SHUTDOWN_PATTERNS = [
    "power down ",
    "systemd-shutdown ",
    "the system is going down for halt ",
    "the system is going down for poweroff ",
]
_POWER_ON_PATTERNS = [
    "created slice system-modprobe.slice ",
    "plymouth-start.service ",
    "reached target basic.target ",
    "reached target sysinit.target ",
    "dracut-initqueue.service ",
]


class SlotManager:
    def __init__(self, registry: Registry):
        self.registry = registry
        self._started = False
        self._state_listeners: list[StateCallback] = []

        # Регистрируем правила на COM1 до старта
        for pc_id, slot in registry.slots.items():
            slot.log_watcher.on(
                _REBOOT_PATTERNS,
                self._make_state_cb(pc_id, "reboot"),
                name=f"sm-reboot-{pc_id}",
            )
            slot.log_watcher.on(
                _SHUTDOWN_PATTERNS,
                self._make_state_cb(pc_id, "shutdown"),
                name=f"sm-shutdown-{pc_id}",
            )
            slot.log_watcher.on(
                _POWER_ON_PATTERNS,
                self._make_state_cb(pc_id, "power_on"),
                name=f"sm-poweron-{pc_id}",
            )

    # ── Подписка для TUI/Оркестратора ─────────────────────
    def on_state_change(self, cb: StateCallback) -> None:
        self._state_listeners.append(cb)

    async def _notify_state(self, pc_id: str, state: str) -> None:
        for cb in self._state_listeners:
            try:
                res = cb(pc_id, state)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                ConsoleLogger.write(
                    f"[SlotManager] state cb error: {e}", LogLevel.ERROR
                )

    # ── Колбэки reboot/power_on ───────────────────────────
    def _make_state_cb(self, pc_id: str, state: str):
        async def cb(line: str) -> None:
            slot = self.registry.slots[pc_id]
            if state == "reboot":
                slot.on_reboot_detected()
                ConsoleLogger.write(
                    f"[{pc_id}] обнаружен reboot", LogLevel.INFO
                )
            elif state == "shutdown":
                slot.on_shutdown_detected()
                ConsoleLogger.write(
                    f"[{pc_id}] обнаружен shutdown", LogLevel.INFO
                )
            elif state == "power_on":
                slot.on_power_on_detected()
                ConsoleLogger.write(
                    f"[{pc_id}] обнаружена загрузка", LogLevel.INFO
                )
            await self._notify_state(pc_id, state)
        return cb

    # ─ Lifecycle ─────────────────────────────────────────
    @property
    def is_started(self) -> bool:
        return self._started

    async def start_all(self):
        """Запустить роутер и все слоты."""
        if self._started:
            return

        # Запускаем роутер, если ещё не запущен
        if not self.registry.router.is_started:
            await self.registry.router.start()

        # Запускаем все слоты
        for slot in self.registry.slots.values():
            await slot.start()

        self._started = True
        ConsoleLogger.write(
            f"[SlotManager] запущено слотов: {len(self.registry.slots)}",
            LogLevel.SUCCESS,
        )

    async def stop_all(self):
        """Остановить все слоты и роутер."""
        if not self._started:
            return

        # Останавливаем слоты
        for slot in self.registry.slots.values():
            try:
                await slot.stop()
            except Exception as e:
                ConsoleLogger.write(
                    f"[SlotManager] stop slot {slot.pc_id}: {e}",
                    LogLevel.ERROR,
                )

        # Останавливаем роутер
        await self.registry.router.stop()

        self._started = False
        ConsoleLogger.write("[SlotManager] остановлен", LogLevel.INFO)

    async def start_slot(self, pc_id: str) -> None:
        """Запустить конкретный слот."""
        slot = self.registry.get(pc_id)
        if slot is None:
            raise KeyError(f"slot {pc_id!r} не найден")
        await slot.start()

    async def stop_slot(self, pc_id: str) -> None:
        """Остановить конкретный слот."""
        slot = self.registry.get(pc_id)
        if slot is None:
            raise KeyError(f"slot {pc_id!r} не найден")
        await slot.stop()

    # ── Получение слота ───────────────────────────────────
    def get_slot(self, pc_id: str) -> Slot | None:
        """Получить слот по pc_id."""
        return self.registry.get(pc_id)

    def all_slots(self) -> dict[str, Slot]:
        """Получить все слоты."""
        return self.registry.slots