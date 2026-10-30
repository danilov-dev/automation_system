"""
Slot — контроллер слота (разъёма).
Тонкий оркестратор, отвечающий за:
— координацию всех компонентов слота
— lifecycle (start, stop)
— выполнение задач (execute)
— маршрутизацию задач через TaskRouter
— РЕАКЦИЮ на смену состояний (перезапуск каналов при STARTING, остановка при OFFLINE)

НЕ отвечает за:
— управление состоянием (это SlotStateController)
— наблюдение за каналами и загрузкой (это SlotMonitor)
— состояние платы как сущности (это Board)
— оркестрацию других слотов (это SlotManager)
"""
import asyncio
import logging
import uuid
from contextlib import suppress
from typing import Optional

from host.app.channels.ws_channel import WsChannel
from host.app.channels.base import Channel, ChannelDead
from host.app.channels.serial_channel import SerialChannel
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
from host.app.slots.exceptions import PendingTask
from host.app.slots.slot_router import TaskRouter
from host.app.slots.state_controller import SlotStateController
from host.app.slots.state_monitor import SlotMonitor


class Slot:
    """
    Контроллер слота (разъёма).
    Тонкий оркестратор, делегирующий управление состоянием SlotStateController,
    а наблюдение — SlotMonitor.
    Реагирует на смену состояний через подписку on_state_change.
    """

    def __init__(
        self,
        slot_id: str,
        ws_channel: WsChannel,
        serial_channel: SerialChannel,
        log_watcher: LogWatcher,
        boot_automation: BootAutomation,
        power_control: PowerControl,
    ):
        self.slot_id = slot_id
        self.ws_channel = ws_channel
        self.serial_channel = serial_channel
        self.log_watcher = log_watcher
        self.boot_automation = boot_automation
        self.power_control = power_control

        # Контроллер состояния (владеет состоянием, платой и метриками)
        self._controller = SlotStateController(
            slot_id=slot_id,
            boot_automation=boot_automation,
        )

        # Монитор (только наблюдает)
        self._monitor = SlotMonitor(
            slot_id=slot_id,
            ws_channel=ws_channel,
            serial_channel=serial_channel,
            log_watcher=log_watcher,
            boot_automation=boot_automation,
            power_control=power_control,  # Передаём power_control в монитор
            controller=self._controller,
        )

        # Маршрутизатор задач
        self._router = TaskRouter(
            slot_id=slot_id,
            channels=self.channels,
            set_offline=lambda reason: self._controller.on_fatal_error(reason),
        )

        # Ожидающие задачи
        self._pending: dict[str, PendingTask] = {}
        self._pumps: list[asyncio.Task] = []
        self._started = False
        self.logger = logging.getLogger(f"slot_{slot_id}")

        # Подписка на события лог-вотчера (парсинг паттернов)
        self._register_log_watcher_rules()

        # Подписка на смену состояния — РЕАКЦИЯ на состояния
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

    # ── Свойства (делегирование к контроллеру) ────────────
    @property
    def name(self) -> str:
        """Алиас для совместимости с TUI."""
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
        return self._controller.is_testing

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

    # Метрики питания (делегирование к контроллеру)
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

    # ── Подписка на смену состояния (делегирование) ───────
    def on_state_change(self, cb) -> None:
        """Зарегистрировать колбэк смены состояния слота."""
        self._controller.on_state_change(cb)

    # ── Обработчики событий лог-вотчера ───────────────────
    async def _on_reboot_line(self, line: str) -> None:
        self._controller.on_reboot_detected()

    async def _on_shutdown_line(self, line: str) -> None:
        self._controller.on_shutdown_detected()

    async def _on_power_on_line(self, line: str) -> None:
        self._controller.on_power_on_detected()

    async def _on_login_prompt_line(self, line: str) -> None:
        self._controller.on_login_prompt_detected()

    # ── Обновление метрик (делегирование) ─────────────────
    def update_metrics(self, v: float, a: float, wt: float) -> None:
        """Обновить показания датчиков питания."""
        self._controller.update_metrics(v, a, wt)

    # ── РЕАКЦИЯ на смену состояния ────────────────────────
    async def _on_state_changed(
        self, slot_id: str, old_state: str, new_state: str
    ) -> None:
        """
        Реакция на смену состояния.
        Оркестрация компонентов в зависимости от нового состояния.
        """
        self.logger.debug(f"State changed: {old_state} -> {new_state}")

        # Если перешли в STARTING (первый запуск или восстановление после OFFLINE)
        if new_state == SlotState.STARTING.name:
            # Если каналы мертвы (например, после обрыва питания), перезапускаем их
            if not self.ws_channel.is_alive or not self.serial_channel.is_alive:
                self.logger.info(
                    "State is STARTING. Restarting channels..."
                )
                await self._restart_channels()

        # Если перешли в OFFLINE, останавливаем каналы, чтобы не жгли ресурсы
        elif new_state == SlotState.OFFLINE.name:
            self.logger.info("State is OFFLINE. Stopping channels...")
            await self._stop_channels()

        # Если перешли в IDLE (после stop), ничего не делаем
        elif new_state == SlotState.IDLE.name:
            self.logger.debug("State is IDLE. Slot is idle.")

    async def _restart_channels(self) -> None:
        """Безопасный перезапуск всех каналов."""
        # Сначала останавливаем, если что-то осталось
        await self._stop_channels()

        try:
            # Запускаем каналы
            try:
                await self.ws_channel.start()
            except Exception as e:
                self.logger.warning(f"WS channel failed to start: {e}")

            try:
                await self.serial_channel.start()
            except Exception as e:
                self.logger.warning(f"Serial channel failed to start: {e}")

            await self.log_watcher.start()

            # Запускаем насосы для чтения отчётов
            self._pumps = [
                asyncio.create_task(
                    self._router.pump(ch, self._pending),
                    name=f"pump-{self.slot_id}-{ch.name}",
                )
                for ch in self.channels
            ]

            # Сообщаем контроллеру, что каналы подняты
            self._controller.on_channels_established()
            self.logger.info("Channels restarted successfully")

        except Exception as e:
            self.logger.error(f"Failed to restart channels: {e}")
            self._controller.on_fatal_error(f"Failed to restart channels: {e}")

    async def _stop_channels(self) -> None:
        """Остановка всех каналов."""
        # Отменяем насосы
        for t in self._pumps:
            t.cancel()
        for t in self._pumps:
            with suppress(asyncio.CancelledError):
                await t
        self._pumps.clear()

        # Останавливаем каналы в обратном порядке
        with suppress(Exception):
            await self.log_watcher.stop()
        with suppress(Exception):
            await self.serial_channel.stop()
            self.boot_automation.is_logged_in = False
        with suppress(Exception):
            await self.ws_channel.stop()

    # ── Lifecycle ─────────────────────────────────────────
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
        await self._monitor.start()

    async def stop(self) -> None:
        """Остановить все компоненты слота."""
        if not self._started:
            return
        self._started = False

        # Останавливаем мониторинг
        await self._monitor.stop()

        # Разбудить все ожидающие задачи
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(
                    ChannelDead(f"[{self.slot_id}] slot stopped")
                )
        self._pending.clear()
        self.logger.debug("pending task list is clear")

        # Останавливаем каналы
        await self._stop_channels()

        # Снимаем питание
        await self.power_control.power_off()

        # Сообщаем контроллеру, что слот остановлен
        self._controller.on_slot_stopped()

        self.logger.info("Slot stopped")

    # ── Публичное API: выполнение задачи ──────────────────
    async def execute(
        self,
        command: str,
        params: Optional[dict] = None,
        progress_timeout: float = 15.0,
    ) -> dict:
        """Отправить задачу и дождаться отчёта."""
        if not self.is_ready:
            raise RuntimeError(
                f"Slot {self.slot_id} is not ready (state: {self._controller.state.name})"
            )

        task_id = uuid.uuid4().hex[:8]
        payload = {
            "type": "task",
            "task_id": task_id,
            "command": command,
            "params": params or {},
        }

        record = PendingTask(task_id, progress_timeout)
        self._pending[task_id] = record

        # Сообщаем контроллеру, что задача началась
        self._controller.on_task_started()

        try:
            report = await self._router.route(payload, record)
            self._controller.apply_report(report)
            return report
        except Exception as e:
            self._controller.on_fatal_error(str(e))
            raise
        finally:
            self._pending.pop(task_id, None)
            if self._controller.state == SlotState.TESTING:
                self._controller.on_task_finished()

    # ── Информация для UI ─────────────────────────────────
    def get_status_dict(self) -> dict:
        return self._controller.get_status_dict(
            ws_alive=self.ws_alive,
            serial_alive=self.serial_alive,
            log_watcher_alive=self.log_watcher_alive,
            is_logged_in=self.is_logged_in,
            last_log_lines=list(self.log_watcher.lines)[-5:],
        )