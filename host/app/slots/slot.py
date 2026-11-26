"""
Slot — контроллер слота (разъёма).
Тонкий оркестратор, отвечающий за:
— координацию всех компонентов слота
— lifecycle (start, stop)
— выполнение тестов через SlotTestRunner
— маршрутизацию задач через TaskRouter
— РЕАКЦИЮ на смену состояний (перезапуск каналов при STARTING, остановка при OFFLINE)
НЕ отвечает за:
— управление состоянием (это SlotStateController)
— наблюдение за каналами и загрузкой (это SlotMonitor)
— состояние платы как сущности (это Board)
— оркестрацию других слотов (это SlotManager)
— выполнение тестов (это SlotTestRunner)
"""
import asyncio
import logging
from contextlib import suppress
from typing import Optional, Callable
from host.app.transports.ws_transport import WebSocketTransport
from host.app.transports.base import Channel
from host.app.transports.serial_transport import SerialTransport
from host.app.hardware.power_control import PowerControl
from host.app.log_watcher import LogWatcher
from host.app.boot_automation import BootAutomation
from host.app.board import Board
from host.app.slots.state import SlotState
from host.app.slots.patterns import (
    REBOOT_PATTERNS,
    SHUTDOWN_PATTERNS,
    POWER_ON_PATTERNS,
    LOGIN_PROMPT_PATTERNS,
)
from host.app.slots.slot_router import TaskRouter
from host.app.slots.test_runner import SlotTestRunner
from host.app.slots.state_controller import SlotStateController
from host.app.slots.state_monitor import SlotMonitor
from host.app.testing.storage import ResultStorage, NullResultStorage
from host.app.testing.test_result import TestResult


class Slot:
    """
    Контроллер слота (разъёма).
    Тонкий оркестратор, делегирующий управление состоянием SlotStateController,
    наблюдение — SlotMonitor, выполнение тестов — SlotTestRunner.
    """

    def __init__(
        self,
        slot_id: str,
        ws_channel: WebSocketTransport,
        serial_channel: SerialTransport,
        log_watcher: LogWatcher,
        boot_automation: BootAutomation,
        power_control: PowerControl,
        storage: Optional[ResultStorage] = None,
    ):
        self.slot_id = slot_id
        self.ws_channel = ws_channel
        self.serial_channel = serial_channel
        self.log_watcher = log_watcher
        self.boot_automation = boot_automation
        self.power_control = power_control

        # Контроллер состояния
        self._controller = SlotStateController(
            slot_id=slot_id,
            boot_automation=boot_automation,
        )

        # Монитор
        self._monitor = SlotMonitor(
            slot_id=slot_id,
            ws_channel=ws_channel,
            serial_channel=serial_channel,
            log_watcher=log_watcher,
            boot_automation=boot_automation,
            power_control=power_control,
            controller=self._controller,
        )

        # Маршрутизатор задач
        self._router = TaskRouter(
            slot_id=slot_id,
            channels=self.channels,
            set_offline=lambda reason: self._controller.on_fatal_error(reason),
            pending_provider=lambda: self._tests.pending,
        )

        # Раннер тестов
        self._tests = SlotTestRunner(
            slot_id=slot_id,
            router=self._router,
            controller=self._controller,
            result_storage=storage or NullResultStorage(),
            progress_timeout=30.0,
        )

        self._started = False
        self.logger = logging.getLogger(f"slot_{slot_id}")

        # Подписка на события лог-вотчера
        self._register_log_watcher_rules()

        # Подписка на смену состояния
        self._controller.on_state_change(self._on_state_changed)

    def _register_log_watcher_rules(self) -> None:
        """Регистрирует правила парсинга логов в LogWatcher."""
        self.log_watcher.register_rule(
            REBOOT_PATTERNS, self._on_reboot_line, name=f"reboot-{self.slot_id}"
        )
        self.log_watcher.register_rule(
            SHUTDOWN_PATTERNS, self._on_shutdown_line, name=f"shutdown-{self.slot_id}"
        )
        self.log_watcher.register_rule(
            POWER_ON_PATTERNS, self._on_power_on_line, name=f"poweron-{self.slot_id}"
        )
        self.log_watcher.register_rule(
            LOGIN_PROMPT_PATTERNS,
            self._on_login_prompt_line,
            name=f"loginprompt-{self.slot_id}",
        )

    # ── Свойства ────────────────────────────────────────
    @property
    def name(self) -> str:
        return self.slot_id

    @property
    def state(self) -> SlotState:
        return self._controller.state

    @property
    def state_duration(self) -> Optional[float]:
        return self._controller.state_duration

    @property
    def is_ready(self) -> bool:
        return self._controller.is_ready

    @property
    def is_testing(self) -> bool:
        return self._tests.is_busy

    @property
    def last_error(self) -> Optional[str]:
        return self._controller.last_error

    @property
    def ws_alive(self) -> bool:
        return self.ws_channel.is_alive

    @property
    def serial_alive(self) -> bool:
        return self.serial_channel.is_alive

    @property
    def log_watcher_alive(self) -> bool:
        return self.log_watcher.is_alive

    @property
    def is_logged_in(self) -> bool:
        return self.boot_automation.is_logged_in

    @property
    def channels(self) -> list[Channel]:
        return [self.ws_channel, self.serial_channel]

    @property
    def board(self) -> Optional[Board]:
        return self._controller.board

    @board.setter
    def board(self, value: Optional[Board]) -> None:
        self._controller.board = value

    @property
    def bios(self) -> Optional[bool]:
        return self._controller.board.bios if self._controller.board else None

    @property
    def os(self) -> Optional[bool]:
        return self._controller.board.os if self._controller.board else None

    @property
    def shell(self) -> Optional[bool]:
        return self._controller.board.shell if self._controller.board else None

    @property
    def v(self) -> float:
        return self._controller.v

    @property
    def a(self) -> float:
        return self._controller.a

    @property
    def wt(self) -> float:
        return self._controller.wt

    @property
    def step(self) -> int:
        return self._controller.step

    @property
    def test_time(self):
        return self._controller.test_time

    @property
    def current_test(self) -> Optional[str]:
        return self._tests.current_command

    # ── Подписка на события ─────────────────────────────
    def on_state_change(self, cb) -> None:
        self._controller.on_state_change(cb)

    def on_power_state_change(self, callback: Callable[[bool], None]) -> None:
        self.power_control.on_power_change(callback)

    def on_metrics_change(self, callback: Callable) -> None:
        self._controller.on_metrics_change(callback)

    def on_board_change(self, callback: Callable) -> None:
        self._controller.on_board_change(callback)

    def on_channels_change(self, callback: Callable) -> None:
        self._controller.on_channels_change(callback)

    def on_progress_change(self, callback: Callable) -> None:
        self._controller.on_progress_change(callback)

    def on_test_result(self, cb) -> None:
        """Подписка на завершение теста."""
        self._tests.on_result(cb)

    # ── Обработчики событий лог-вотчера ─────────────────
    async def _on_reboot_line(self, line: str) -> None:
        self._controller.on_reboot_detected()

    async def _on_shutdown_line(self, line: str) -> None:
        self._controller.on_shutdown_detected()

    async def _on_power_on_line(self, line: str) -> None:
        self._controller.on_power_on_detected()

    async def _on_login_prompt_line(self, line: str) -> None:
        self._controller.on_login_prompt_detected()

    # ── Обновление метрик ───────────────────────────────
    def update_metrics(self, v: float, a: float, wt: float) -> None:
        self._controller.notify_update_metrics(v, a, wt)

    # ── РЕАКЦИЯ на смену состояния ──────────────────────
    async def _on_state_changed(
        self, slot_id: str, old_state: str, new_state: str
    ) -> None:
        self.logger.debug(f"State changed: {old_state} -> {new_state}")

        if new_state == SlotState.STARTING.name:
            if not self.ws_channel.is_alive or not self.serial_channel.is_alive:
                self.logger.info("State is STARTING. Restarting transports...")
                await self._restart_channels()

        elif new_state == SlotState.OFFLINE.name:
            self.logger.info("State is OFFLINE. Stopping transports...")
            self._tests.abort("slot went offline")
            await self._stop_channels()

        elif new_state == SlotState.ERROR.name:
            self._tests.abort(f"slot error: {self.last_error}")

        elif new_state == SlotState.IDLE.name:
            self.logger.debug("State is IDLE. Slot is idle.")

    async def _restart_channels(self) -> None:
        """Безопасный перезапуск всех каналов."""
        await self._stop_channels()
        try:
            try:
                await self.ws_channel.start()
            except Exception as e:
                self.logger.warning(f"WS channel failed to start: {e}")
            try:
                await self.serial_channel.start()
            except Exception as e:
                self.logger.warning(f"Serial channel failed to start: {e}")
            await self.log_watcher.start()

            self._router.start_pumps()
            self._controller.on_channels_established()
            self.logger.info("Channels restarted successfully")
        except Exception as e:
            self.logger.error(f"Failed to restart transports: {e}")
            self._controller.on_fatal_error(f"Failed to restart transports: {e}")

    async def _stop_channels(self) -> None:
        """Остановка всех каналов."""
        await self._router.stop_pumps()
        with suppress(Exception):
            await self.log_watcher.stop()
        with suppress(Exception):
            await self.serial_channel.stop()
            self.boot_automation.is_logged_in = False
        with suppress(Exception):
            await self.ws_channel.stop()

    # ── Lifecycle ───────────────────────────────────────
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._controller.on_start_requested()
        await self.power_control.power_on()
        if not self.power_control.is_on:
            self._controller.on_fatal_error(f"The slot '{self.slot_id}' did not receive power.")
            self._started = False
            raise Exception(f"The slot '{self.slot_id}' did not receive power.")
        self._router.start_pumps()
        await self._monitor.start()

    async def stop(self) -> None:
        """Остановить все компоненты слота."""
        if not self._started:
            return
        self._started = False
        self._tests.abort(f"[{self.slot_id}] slot stopped")
        await self._monitor.stop()
        await self._stop_channels()
        await self.power_control.power_off()
        self._controller.on_slot_stopped()
        self.logger.info("Slot stopped")

    # ── Публичное API: тесты ────────────────────────────
    async def run_tests(
        self,
        commands: list[str],
        params: Optional[dict] = None,
        stop_on_error: bool = True,
    ) -> list[TestResult]:
        """Выполнить набор тестов на этом слоте."""
        return await self._tests.run_suite(commands, params, stop_on_error)

    async def execute(
        self,
        command: str,
        params: Optional[dict] = None,
        progress_timeout: Optional[float] = None,
    ) -> TestResult:
        """Выполнить один тест."""
        return await self._tests.run_one(command, params, progress_timeout)

    def cancel_tests(self) -> None:
        """Отменить текущую сессию тестов."""
        self._tests.cancel()

    def get_history(self) -> list[TestResult]:
        """Получить историю результатов тестов."""
        return list(self._tests._result_storage.load(self.slot_id))

    # ── Информация для UI ───────────────────────────────
    def get_status_dict(self) -> dict:
        return self._controller.get_status_dict(
            ws_alive=self.ws_alive,
            serial_alive=self.serial_alive,
            log_watcher_alive=self.log_watcher_alive,
            is_logged_in=self.is_logged_in,
            last_log_lines=list(self.log_watcher.lines)[-5:],
        )