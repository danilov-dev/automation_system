"""
SessionManager — жизненный цикл стенда.

Ответственности:
  1. start_all / stop_all: поднять/погасить router + все PcSession + LogWatcher.
  2. Подписка на события COM1 (reboot/shutdown/power_on) — сброс состояния слота.
  3. Тонкое API для TUI: execute(pc_id, command, params).
  4. Опционально — уведомлять внешних подписчиков об изменении состояния слота.
"""
import asyncio
from typing import Awaitable, Callable, Union

from app.registry import Registry
from app.utils.console_logger import ConsoleLogger, LogLevel


# Колбэк о смене состояния слота: (pc_id, state_name)
StateCallback = Callable[[str, str], Union[None, Awaitable[None]]]


# Паттерны для COM1. Совпадают с DiagnosticRulesFactory,
# но продублированы явно — SessionManager не должен зависеть от неё.
_REBOOT_PATTERNS = [
    "reboot: restarting system",
    "systemd-reboot.service",
    "reached target reboot.target",
    "the system is going down for reboot",
]
_SHUTDOWN_PATTERNS = [
    "power down",
    "systemd-shutdown",
    "the system is going down for halt",
    "the system is going down for poweroff",
]
_POWER_ON_PATTERNS = [
    "created slice system-modprobe.slice",
    "plymouth-start.service",
    "reached target basic.target",
    "reached target sysinit.target",
    "dracut-initqueue.service",
]


class SessionManager:
    def __init__(self, registry: Registry):
        self.registry = registry
        self._started = False
        self._state_listeners: list[StateCallback] = []

        # регистрируем правила на COM1 до старта — on() безопасен на «холодном» watcher
        for pc_id, bundle in registry.slots.items():
            bundle.log_watcher.on(
                _REBOOT_PATTERNS,
                self._make_state_cb(pc_id, "reboot"),
                name=f"sm-reboot-{pc_id}",
            )
            bundle.log_watcher.on(
                _SHUTDOWN_PATTERNS,
                self._make_state_cb(pc_id, "shutdown"),
                name=f"sm-shutdown-{pc_id}",
            )
            bundle.log_watcher.on(
                _POWER_ON_PATTERNS,
                self._make_state_cb(pc_id, "power_on"),
                name=f"sm-poweron-{pc_id}",
            )

    # ── подписка для TUI/Оркестратора ─────────────────────

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
                    f"[SessionManager] state cb error: {e}", LogLevel.ERROR
                )

    # ── колбэки reboot/power_on ───────────────────────────

    def _make_state_cb(self, pc_id: str, state: str):
        async def cb(line: str) -> None:
            bundle = self.registry.slots[pc_id]
            if state == "reboot":
                bundle.boot_automation.reset()
                ConsoleLogger.write(
                    f"[{pc_id}] обнаружен reboot", LogLevel.INFO
                )
            elif state == "shutdown":
                bundle.boot_automation.reset()
                ConsoleLogger.write(
                    f"[{pc_id}] обнаружен shutdown", LogLevel.INFO
                )
            elif state == "power_on":
                bundle.boot_automation.reset()
                ConsoleLogger.write(
                    f"[{pc_id}] обнаружена загрузка", LogLevel.INFO
                )
            await self._notify_state(pc_id, state)
        return cb

    # ── lifecycle ─────────────────────────────────────────

    @property
    def is_started(self) -> bool:
        return self._started

    async def start_all(self) -> None:
        if self._started:
            return

        # 1. Router первым — TCP-каналы должны иметь куда attach_writer'а
        await self.registry.router.start()

        # 2. PcSession — открывают serial, поднимают pump'ы, TCP уже слушается
        for bundle in self.registry.slots.values():
            await bundle.pc_session.start()

        # 3. LogWatcher — открываем последним, чтобы если что-то упадёт выше,
        #    COM1 не остался «висеть» без потребителей
        for bundle in self.registry.slots.values():
            await bundle.log_watcher.start()

        self._started = True
        ConsoleLogger.write(
            f"[SessionManager] запущено слотов: {len(self.registry.slots)}",
            LogLevel.SUCCESS,
        )

    async def stop_all(self) -> None:
        if not self._started:
            return

        # обратный порядок
        for bundle in self.registry.slots.values():
            try:
                await bundle.log_watcher.stop()
            except Exception as e:
                ConsoleLogger.write(
                    f"[SessionManager] stop watcher {bundle.pc_id}: {e}",
                    LogLevel.ERROR,
                )
        for bundle in self.registry.slots.values():
            try:
                await bundle.pc_session.stop()
            except Exception as e:
                ConsoleLogger.write(
                    f"[SessionManager] stop session {bundle.pc_id}: {e}",
                    LogLevel.ERROR,
                )
        await self.registry.router.stop()

        self._started = False
        ConsoleLogger.write("[SessionManager] остановлен", LogLevel.INFO)

    # ── API для TUI ───────────────────────────────────────

    async def execute(
        self,
        pc_id: str,
        command: str,
        params: dict | None = None,
        progress_timeout: float = 15.0,
    ) -> dict:
        bundle = self.registry.get(pc_id)
        if bundle is None:
            raise KeyError(f"slot {pc_id!r} не найден")
        return await bundle.pc_session.execute(
            command, params, progress_timeout=progress_timeout
        )

    def get_session(self, pc_id: str):
        b = self.registry.get(pc_id)
        return b.pc_session if b else None