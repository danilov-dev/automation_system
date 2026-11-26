"""
SlotMonitor — наблюдение за здоровьем слота.
Отвечает ТОЛЬКО за наблюдение и генерацию семантических событий.
Наблюдает за:
— состоянием питания (опрашивает PowerControl.is_on)
— живостью каналов (WS, Serial, LogWatcher)
— успешностью логина (через BootAutomation)
— откликом устройства через COM-порт (проба)
НЕ принимает решений о состояниях напрямую.
Вместо этого вызывает семантические методы SlotStateController:
— on_power_lost() / on_power_restored()
— on_fatal_error()
— on_login_success()
— on_board_alive()
НЕ отвечает за:
— изменение состояния слота напрямую (это SlotStateController)
— изменение состояния платы напрямую (это SlotStateController)
— выполнение задач (это Slot + TaskRouter)
— перезапуск каналов (это Slot, по событию on_state_change)
"""
import asyncio
import logging
from typing import Optional

from host.app.slots.state import SlotState
from host.app.slots.state_controller import SlotStateController
from host.app.boot_automation import BootAutomation
from host.app.transports.ws_transport import WebSocketTransport
from host.app.transports.serial_transport import SerialTransport
from host.app.log_watcher import LogWatcher
from host.app.hardware.power_control import PowerControl


class SlotMonitor:
    """Наблюдение за здоровьем слота: питание, каналы, загрузка."""

    def __init__(
            self,
            slot_id: str,
            ws_channel: WebSocketTransport,
            serial_channel: SerialTransport,
            log_watcher: LogWatcher,
            boot_automation: BootAutomation,
            power_control: PowerControl,
            controller: SlotStateController,
    ):
        self.slot_id = slot_id
        self.ws_channel = ws_channel
        self.serial_channel = serial_channel
        self.log_watcher = log_watcher
        self.boot_automation = boot_automation
        self.power_control = power_control
        self.controller = controller

        # Задачи мониторинга
        self._watch_boot_task: Optional[asyncio.Task] = None
        self._watch_power_and_channels_task: Optional[asyncio.Task] = None
        self._started = False
        self.logger = logging.getLogger(f"slot_{slot_id}")

    # ── Lifecycle мониторинга ─────────────────────────────
    async def start(self) -> None:
        """Запустить задачи мониторинга."""
        if self._started:
            return
        self._started = True

        self.power_control.on_metrics_change(self._on_power_metrics)

        self._watch_boot_task = asyncio.create_task(
            self._watch_boot(), name=f"watch-boot-{self.slot_id}"
        )
        self._watch_power_and_channels_task = asyncio.create_task(
            self._watch_power_and_channels(),
            name=f"watch-power-channels-{self.slot_id}",
        )

    async def stop(self) -> None:
        """Остановить задачи мониторинга."""
        self._started = False
        for task in (self._watch_boot_task, self._watch_power_and_channels_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._watch_boot_task = None
        self._watch_power_and_channels_task = None

    def _on_power_metrics(self, v: float, a: float, wt: float) -> None:
        self.controller.notify_update_metrics(v, a, wt)

    # ── Наблюдение за загрузкой ───────────────────────────
    async def _watch_boot(self) -> None:
        """
        Периодически проверяет успешность логина и живость платы.
        Только наблюдает и вызывает семантические методы контроллера.
        НЕ меняет состояние напрямую.
        """
        was_logged = False
        while self._started:
            # Если WS жив и плата есть — сообщаем контроллеру о живости платы.
            # Контроллер сам решит, менять ли board.bios/os и уведомлять ли подписчиков.
            if self.ws_channel.is_alive and self.controller.board:
                self.controller.on_board_alive()

            # Наблюдаем за логином
            is_logged = self.boot_automation.is_logged_in
            if is_logged and not was_logged:
                self.controller.on_login_success()
            was_logged = is_logged

            await asyncio.sleep(0.5)

    # ── Наблюдение за питанием и каналами ─────────────────
    async def _watch_power_and_channels(self) -> None:
        """
        Опрос питания и каналов.
        При изменении состояния питания вызывает семантические методы контроллера.
        При изменении живости каналов сообщает контроллеру.
        При потере связи — проверяет устройство через COM.
        """
        power_was_on = self.power_control.is_on

        # Кэш живости для детектирования изменений
        prev_ws = self.ws_channel.is_alive
        prev_serial = self.serial_channel.is_alive
        prev_log = self.log_watcher.is_alive

        while self._started:
            power_is_on = self.power_control.is_on
            ws_alive = self.ws_channel.is_alive
            serial_alive = self.serial_channel.is_alive
            log_alive = self.log_watcher.is_alive
            state = self.controller.state

            # ── Детектируем изменение живости каналов ──────
            if (ws_alive, serial_alive, log_alive) != (prev_ws, prev_serial, prev_log):
                self.controller.on_channels_status_changed(ws_alive, serial_alive, log_alive)
                prev_ws, prev_serial, prev_log = ws_alive, serial_alive, log_alive

            # ── 1. Обрыв питания ──────────────────────────
            if power_was_on and not power_is_on:
                self.controller.on_power_lost("Hardware power lost")
                power_was_on = power_is_on
                await asyncio.sleep(1.0)
                continue

            # ── 2. Восстановление питания ─────────────────
            if not power_was_on and power_is_on:
                self.controller.on_power_restored()
                power_was_on = power_is_on
                await asyncio.sleep(2.0)
                continue

            # Дальше работаем только если питание включено
            if not power_is_on:
                await asyncio.sleep(1.0)
                continue

            # ── 3. Мы были в READY/TESTING, но WS отвалился──
            if state in (SlotState.READY, SlotState.TESTING) and not ws_alive:
                self.logger.warning(
                    "WS lost. Probing device via COM to check if it's truly offline..."
                )
                is_responsive = await self._probe_device(timeout=2.0)
                if is_responsive:
                    self.logger.info(
                        "Device responded to probe. Network glitch only. Staying in current state."
                    )
                else:
                    self.logger.warning(
                        "Device did not respond to probe. Going to ERROR."
                    )
                    self.controller.on_fatal_error(
                        "WS lost and device unresponsive via COM"
                    )

            elif state == SlotState.OFFLINE and ws_alive:
                self.logger.debug(
                    "WS is up while OFFLINE. Waiting for power restore event..."
                )

            await asyncio.sleep(1.0)

    # ── Проба устройства ──────────────────────────────────
    async def _probe_device(self, timeout: float = 2.0) -> bool:
        """
        'Стучится' в COM-порт, чтобы проверить, живо ли устройство.
        Отправляет \\n и ждет, пока BootAutomation не обнаружит приглашение оболочки.
        """
        if not self.log_watcher.is_alive:
            return False

        # Запоминаем состояние, чтобы восстановить его ТОЛЬКО при неудаче
        was_logged = self.boot_automation.is_logged_in

        # Временно сбрасываем флаги, чтобы отловить свежую реакцию на наш "стук"
        self.boot_automation.is_logged_in = False
        self.boot_automation._auth_sent = False

        # Стучимся
        await self.log_watcher.send_line("\n")

        start_time = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - start_time < timeout:
            if self.boot_automation.is_logged_in:
                return True
            await asyncio.sleep(0.1)

        # Не удалось — восстанавливаем старое состояние
        self.boot_automation.is_logged_in = was_logged
        return False
