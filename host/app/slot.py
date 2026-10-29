"""
Slot — контроллер слота (разъёма).
Отвечает за:
  - каналы связи с платой (WS, Serial)
  - датчики питания (v, a, wt) — пока заглушки, будут драйвером
  - питание платы (реле)
  - наблюдение за загрузкой платы (через log_watcher)
  - выполнение задач на плате (через каналы)
НЕ отвечает за:
  - состояние платы как сущности (это делает Board)
  - оркестрацию других слотов (это делает SlotManager)
"""
import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Optional, Callable, Awaitable, Union

from host.app.channels.ws_channel import WsChannel
from host.app.channels.base import Channel, ChannelDead, ChannelError
from host.app.channels.serial_channel import SerialChannel
from host.app.hardware.power_control import PowerControl
from host.app.log_watcher import LogWatcher
from host.app.boot_automation import BootAutomation
from host.app.board import Board

# ═══════════════════════════════════════════════════════════
# Паттерны для наблюдения за загрузкой платы через COM1
# ═══════════════════════════════════════════════════════════
_BIOS_PATTERNS = [
    "PCIe: type 1",
    "BIOS",
]
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
_LOGIN_PROMPT_PATTERNS = [
    "login:",
]


# ═══════════════════════════════════════════════════════════
# Исключения
# ═══════════════════════════════════════════════════════════
class AllChannelsFailed(ChannelError):
    """Ни один канал не смог доставить задачу и получить отчёт."""

    def __init__(
            self,
            slot_id: str,
            task_id: str,
            tried: list[str],
            last_err: Exception | None,
    ):
        msg = (
            f"[{slot_id}] task {task_id} не выполнен. "
            f"tried={tried}, last_err={last_err!r}"
        )
        super().__init__(msg)
        self.slot_id = slot_id
        self.task_id = task_id
        self.tried = tried
        self.last_err = last_err


# ═══════════════════════════════════════════════════════════
# Внутренние классы
# ═══════════════════════════════════════════════════════════
class _PendingTask:
    """Состояние ожидания одного отчёта."""

    __slots__ = ("task_id", "progress_timeout", "final", "progress")

    def __init__(self, task_id: str, progress_timeout: float):
        self.task_id = task_id
        self.progress_timeout = progress_timeout
        self.final: asyncio.Future = asyncio.get_running_loop().create_future()
        self.progress = asyncio.Event()

    def on_report(self, msg: dict) -> None:
        if msg.get("status") == "in_progress":
            self.progress.set()
            return
        if not self.final.done():
            self.final.set_result(msg)


# ═══════════════════════════════════════════════════════════
# Состояния слота
# ═══════════════════════════════════════════════════════════
class SlotState(Enum):
    IDLE = auto()  # Инициализирован, но не запущен
    STARTING = auto()  # Запускается
    CONNECTING = auto()  # Ждёт подключения клиента (WS handshake)
    READY = auto()  # Готов к работе
    TESTING = auto()  # Выполняет тест
    REBOOTING = auto()  # Перезагружается
    SHUTDOWN = auto()  # Выключается
    ERROR = auto()  # Ошибка
    OFFLINE = auto()  # Каналы недоступны


# ═══════════════════════════════════════════════════════════
# Slot
# ═══════════════════════════════════════════════════════════
class Slot:
    """
    Контроллер слота (разъёма).
    Держит каналы, датчики, питание. Наблюдает за платой.
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

        self._state = SlotState.IDLE
        self._state_changed_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._pending: dict[str, _PendingTask] = {}
        self._pumps: list[asyncio.Task] = []
        self._started = False
        self._boot_watcher: Optional[asyncio.Task] = None

        # Текущая плата в слоте (может быть заменена при перестановке)
        self.board: Board | None = None

        # Датчики питания (пока заглушки, будут драйвером)
        self.v: float = 0.0
        self.a: float = 0.0
        self.wt: float = 0.0

        # Состояние текущего теста
        self.step: int = 0
        self.test_time: timedelta = timedelta()

        # Колбэки смены состояния (для SlotManager и внешних клиентов)
        self._state_callbacks: list[Callable[[str, str], Union[None, Awaitable[None]]]] = []
        self._channel_watcher: Optional[asyncio.Task] = None

        self.logger = logging.getLogger(f"slot_{slot_id}")

        # Подписка на события лог-вотчера (парсинг паттернов)
        self.log_watcher.register_rule(
            _REBOOT_PATTERNS,
            self._on_reboot_line,
            name=f"reboot-{slot_id}",
        )
        self.log_watcher.register_rule(
            _SHUTDOWN_PATTERNS,
            self._on_shutdown_line,
            name=f"shutdown-{slot_id}",
        )
        self.log_watcher.register_rule(
            _POWER_ON_PATTERNS,
            self._on_power_on_line,
            name=f"poweron-{slot_id}",
        )
        self.log_watcher.register_rule(
            _LOGIN_PROMPT_PATTERNS,
            self._on_login_prompt_line,
            name=f"loginprompt-{slot_id}",
        )

    # ── Свойства ──────────────────────────────────────────
    @property
    def name(self) -> str:
        """Алиас для совместимости с TUI."""
        return self.slot_id

    @property
    def state(self) -> SlotState:
        return self._state

    @property
    def state_duration(self) -> Optional[float]:
        if self._state_changed_at is None:
            return None
        return (datetime.now() - self._state_changed_at).total_seconds()

    @property
    def is_ready(self) -> bool:
        return self._state == SlotState.READY

    @property
    def is_testing(self) -> bool:
        return self._state == SlotState.TESTING

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ── Статусы каналов ──────────────────────────────────
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

    # ── Подписка на смену состояния ──────────────────────
    def on_state_change(
            self, cb: Callable[[str, str], Union[None, Awaitable[None]]]
    ) -> None:
        """Зарегистрировать колбэк смены состояния слота."""
        self._state_callbacks.append(cb)

    # ── Управление состоянием ─────────────────────────────
    def set_state(self, new_state: SlotState, error: Optional[str] = None):
        old_state = self._state
        self._state = new_state
        self._state_changed_at = datetime.now()
        if error:
            self._last_error = error

        if old_state != new_state:
            self.logger.info(f"State: {old_state.name} -> {new_state.name}")
            # Уведомляем подписчиков
            for cb in self._state_callbacks:
                try:
                    res = cb(self.slot_id, new_state.name)
                    if asyncio.iscoroutine(res):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(res)
                        except RuntimeError:
                            pass
                except Exception as e:
                    self.logger.error(f"state cb error: {e}")

    # ── Обработчики событий лог-вотчера ───────────────────
    async def _on_reboot_line(self, line: str) -> None:
        self.on_reboot_detected()

    async def _on_shutdown_line(self, line: str) -> None:
        self.on_shutdown_detected()

    async def _on_power_on_line(self, line: str) -> None:
        self.on_power_on_detected()

    async def _on_login_prompt_line(self, line: str) -> None:
        self.on_login_prompt_detected()

    # ── Реакция на события платы ──────────────────────────
    def on_reboot_detected(self):
        """Обнаружена перезагрузка платы."""
        self.set_state(SlotState.REBOOTING)
        self.boot_automation.reset()
        if self.board:
            self.board.reset()

        self.logger.debug(f"reboot detected")

    def on_shutdown_detected(self):
        """Обнаружено выключение платы."""
        self.set_state(SlotState.SHUTDOWN)
        if self.board:
            self.board.reset()

        self.logger.debug(f"shutdown detected")

    def on_power_on_detected(self):
        """Обнаружено включение платы (начало загрузки)."""
        if self._state == SlotState.REBOOTING:
            self.set_state(SlotState.CONNECTING)
        if self.board:
            self.board.bios = True
            self.board.touch()

        self.logger.debug(f"Board started detected")

    def on_login_prompt_detected(self):
        """Обнаружено приглашение логина — ОС загрузилась."""
        if self.board:
            self.board.os = True
            self.board.touch()

        self.logger.debug(f"login prompt detected")

    def on_login_success(self):
        """Обнаружен успешный логин в шелл."""
        if self.board:
            self.board.os = True
            self.board.shell = True
            self.board.touch()
        if self._state in (SlotState.STARTING, SlotState.CONNECTING, SlotState.REBOOTING):
            self.set_state(SlotState.READY)

        self.logger.debug(f"login success detected")


    async def _probe_device(self, timeout: float = 2.0) -> bool:
        """
        'Стучится' в COM-порт, чтобы проверить, живо ли устройство.
        Отправляет \\n и ждет, пока BootAutomation не обнаружит приглашение оболочки.
        """
        if not self.log_watcher.is_alive:
            return False

        # Запоминаем состояние, чтобы восстановить его, если проба неудачна
        was_logged = self.boot_automation.is_logged_in

        # Временно сбрасываем флаги, чтобы отловить свежую реакцию на наш "стук"
        self.boot_automation.is_logged_in = False
        self.boot_automation._auth_sent = False

        # Стучимся
        await self.log_watcher.send_line('\n')

        # Ждем реакции
        start_time = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - start_time < timeout:
            if self.boot_automation.is_logged_in:
                self.boot_automation.is_logged_in = was_logged
                return True
            await asyncio.sleep(0.1)

        # Реакции не было. Восстанавливаем исходный флаг и возвращаем False
        self.boot_automation.is_logged_in = was_logged
        return False

    # ── Мониторинг логина ─────────────────────────────────
    async def _watch_boot(self):
        """Периодически проверяет успешность логина и состояние каналов."""
        was_logged = False
        while self._started:
            if self.ws_alive and self.board:
                if not self.board.bios:
                    self.board.bios = True
                    self.board.touch()
                if not self.board.os:
                    self.board.os = True
                    self.board.touch()

            is_logged = self.boot_automation.is_logged_in
            if is_logged and not was_logged:
                self.on_login_success()
            was_logged = is_logged
            await asyncio.sleep(0.5)

    async def _watch_channels(self):
        """Мониторинг каналов с программной 'пробой' устройства при сбое сети."""
        while self._started:
            ws_alive = self.ws_channel.is_alive

            # 1. Мы были в READY, но WS отвалился. Проверяем, не выключили ли устройство.
            if self._state == SlotState.READY and not ws_alive:
                self.logger.warning("WS lost. Probing device via COM to check if it's truly offline...")
                is_responsive = await self._probe_device(timeout=2.0)

                if is_responsive:
                    self.logger.info("Device responded to probe. Network glitch only. Staying READY.")
                    # Устройство живо, просто сеть моргнула. WS переподключится сам.
                else:
                    self.logger.warning("Device did not respond to probe. Going OFFLINE.")
                    self.set_state(SlotState.OFFLINE, "WS lost and device unresponsive via COM")
                    if self.board:
                        self.board.reset()
                        self.boot_automation.reset()

            # 2. Мы в OFFLINE, и WS снова появился. Проверяем, загрузилось ли устройство.
            elif self._state == SlotState.OFFLINE and ws_alive:
                self.logger.info("WS recovered. Probing device via COM before attempting recovery...")
                is_responsive = await self._probe_device(timeout=2.0)

                if is_responsive:
                    self.logger.info("Device is alive! Resetting boot state and reconnecting.")
                    self.boot_automation.reset()
                    if not self.state in [SlotState.READY]:
                        self.set_state(SlotState.CONNECTING)
                else:
                    self.logger.debug("WS is up, but device is still unresponsive. Staying OFFLINE.")

            # 3. Защита от зависания в промежуточных состояниях, если всё умерло
            elif self._state in (SlotState.STARTING, SlotState.REBOOTING, SlotState.CONNECTING):
                if not ws_alive and not self.log_watcher.is_alive:
                    self.set_state(SlotState.OFFLINE, "All channels lost during boot")

            await asyncio.sleep(1.0)

    # ── Обновление датчиков и метрик ──────────────────────
    def update_metrics(self, v: float, a: float, wt: float) -> None:
        """Обновить показания датчиков питания."""
        self.v = v
        self.a = a
        self.wt = wt

    def _apply_report(self, report: dict) -> None:
        """Применить данные из отчёта к состоянию слота."""
        result = report.get("result", {})
        if not isinstance(result, dict):
            return
        if "step" in result:
            self.step = int(result["step"])
            self.test_time = timedelta(seconds=self.step * 5)
        if "v" in result:
            self.v = float(result["v"])
        if "a" in result:
            self.a = float(result["a"])
        if "wt" in result:
            self.wt = float(result["wt"])

    # ── Lifecycle ─────────────────────────────────────────
    async def start(self):
        if self._started:
            return
        self.set_state(SlotState.STARTING)
        await self.power_control.power_on()
        if not self.power_control.is_on:
            self.set_state(SlotState.ERROR)
            raise Exception(f"The slot '{self.slot_id}' did not receive power.")

        self._started = True
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
                    self._pump(ch), name=f"pump-{self.slot_id}-{ch.name}"
                )
                for ch in self.channels
            ]

            # Запускаем мониторинг логина
            self._boot_watcher = asyncio.create_task(self._watch_boot())

            # Переходим в CONNECTING — дальше ждём события из log_watcher
            self.set_state(SlotState.CONNECTING)

            self._channel_watcher = asyncio.create_task(self._watch_channels())
        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            self._started = False
            self.logger.error(f"WS channel failed to start: {e}")
            raise

    async def stop(self):
        """Остановить все компоненты слота."""
        if not self._started:
            return
        self._started = False

        # Отменить мониторинг каналов
        if self._channel_watcher:
            self._channel_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await self._channel_watcher
            self._channel_watcher = None

        # Отменить мониторинг логина
        if self._boot_watcher:
            self.logger.debug(f"WS boot_watcher canceling")
            self._boot_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await self._boot_watcher
            self._boot_watcher = None
            self.logger.debug(f"WS boot_watcher canceled")

        # Разбудить все ожидающие задачи
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(
                    ChannelDead(f"[{self.slot_id}] slot stopped")
                )
        self._pending.clear()
        self.logger.debug(f"pending task list is clear")

        # Отменить насосы
        self.logger.debug(f"stopping all pumps")
        for t in self._pumps:
            t.cancel()
        for t in self._pumps:
            with suppress(asyncio.CancelledError):
                await t
        self._pumps.clear()
        self.logger.debug(f"all pumps are stopped")

        # Остановить каналы в обратном порядке
        try:
            await self.log_watcher.stop()
        except Exception as e:
            self.logger.error(f"log_watcher stop error: {e}")
        try:
            await self.serial_channel.stop()
            self.boot_automation.is_logged_in = False
        except Exception as e:
            self.logger.error(f"serial stop error: {e}")
        try:
            await self.ws_channel.stop()
        except Exception as e:
            self.logger.error("ws stop error: {e}")

        await self.power_control.power_off()
        self.set_state(SlotState.IDLE)

        # Сбросить плату
        if self.board:
            self.board.reset()

        self.logger.info(f"Slot stopped")

    # ── Публичное API: выполнение задачи ──────────────────
    async def execute(
            self,
            command: str,
            params: dict | None = None,
            progress_timeout: float = 15.0,
    ) -> dict:
        """Отправить задачу и дождаться отчёта."""
        if not self.is_ready:
            raise RuntimeError(
                f"Slot {self.slot_id} is not ready (state: {self._state.name})"
            )

        task_id = uuid.uuid4().hex[:8]
        payload = {
            "type": "task",
            "task_id": task_id,
            "command": command,
            "params": params or {},
        }
        record = _PendingTask(task_id, progress_timeout)
        self._pending[task_id] = record
        self.set_state(SlotState.TESTING)
        try:
            report = await self._route(payload, record)
            # Обновляем состояние из отчёта
            self._apply_report(report)
            return report
        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            raise
        finally:
            self._pending.pop(task_id, None)
            if self._state == SlotState.TESTING:
                self.set_state(SlotState.READY)

    # ── Маршрутизация (фейловер) ──────────────────────────
    async def _wait_report(self, record: _PendingTask, ch: Channel) -> dict:
        """Ждём финальный отчёт. Каждый in_progress продлевает окно."""
        while True:
            record.progress.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.final),
                    timeout=record.progress_timeout,
                )
            except asyncio.TimeoutError:
                if not ch.is_alive:
                    raise ChannelDead(
                        f"{ch.name} died while waiting {record.task_id}"
                    )
                if not record.progress.is_set():
                    raise
                continue
            return record.final.result()

    async def _route(self, payload: dict, record: _PendingTask) -> dict:
        tried: list[str] = []
        last_err: Exception | None = None

        for ch in self.channels:
            if not ch.is_alive:
                continue
            tried.append(ch.name)
            try:
                await ch.send(payload)
                self.logger.info(f"task {record.task_id} -> {ch.name}")
            except ChannelDead as e:
                last_err = e
                self.logger.warning(f"send {ch.name} fail: {e}")
                continue

            try:
                report = await self._wait_report(record, ch)
                self.logger.info(f"task {record.task_id} <- {ch.name} ({report.get("status")})")
                return report
            except asyncio.TimeoutError as e:
                last_err = e
                self.logger.warning(
                    f"The channel:{ch.name}  has exceeded the timeout limit: {record.task_id}, Trying another one")
                continue
            except ChannelDead as e:
                last_err = e
                self.logger.warning(
                    f"The channel: {ch.name} died while the task: {record.task_id} was being executed. Trying another one")
                continue

        if not any(ch.is_alive for ch in self.channels):
            self.set_state(SlotState.OFFLINE, "All channels failed")
        raise AllChannelsFailed(self.slot_id, record.task_id, tried, last_err)

    # ── Насос: чтение отчётов из каналов ──────────────────
    async def _pump(self, ch: Channel) -> None:
        try:
            async for msg in ch.incoming():
                mtype = msg.get("type")
                if mtype not in ("report", "error"):
                    continue
                tid = msg.get("task_id")
                if not tid:
                    continue
                record = self._pending.get(tid)
                if record is None:
                    continue
                record.on_report(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"pump {ch.name} is broken: {e}", self.slot_id, ch.name, e)

    # ── Информация для UI ─────────────────────────────────
    def get_status_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "state": self._state.name,
            "state_duration": self.state_duration,
            "ws_alive": self.ws_alive,
            "serial_alive": self.serial_alive,
            "log_watcher_alive": self.log_watcher_alive,
            "is_logged_in": self.is_logged_in,
            "last_error": self._last_error,
            "last_log_lines": list(self.log_watcher.lines)[-5:],
        }
