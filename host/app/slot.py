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
from host.app.utils.console_logger import ConsoleLogger, LogLevel


# ═══════════════════════════════════════════════════════════════
# Паттерны для наблюдения за загрузкой платы через COM1
# ═══════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════
# Исключения
# ═══════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════
# Внутренние классы
# ═══════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════
# Состояния слота
# ═══════════════════════════════════════════════════════════════
class SlotState(Enum):
    IDLE = auto()          # Инициализирован, но не запущен
    STARTING = auto()      # Запускается
    CONNECTING = auto()    # Ждёт подключения клиента (WS handshake)
    READY = auto()         # Готов к работе
    TESTING = auto()       # Выполняет тест
    REBOOTING = auto()     # Перезагружается
    SHUTDOWN = auto()      # Выключается
    ERROR = auto()         # Ошибка
    OFFLINE = auto()       # Каналы недоступны


# ═══════════════════════════════════════════════════════════════
# Slot
# ═══════════════════════════════════════════════════════════════
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

    # ── Свойства ──────────────────────────────────────────────
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

    # ── Статусы каналов ──────────────────────────────────────
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

    # ── Подписка на смену состояния ──────────────────────────
    def on_state_change(self, cb: Callable[[str, str], Union[None, Awaitable[None]]]) -> None:
        """Зарегистрировать колбэк смены состояния слота."""
        self._state_callbacks.append(cb)

    # ── Управление состоянием ─────────────────────────────────
    def set_state(self, new_state: SlotState, error: Optional[str] = None):
        old_state = self._state
        self._state = new_state
        self._state_changed_at = datetime.now()
        if error:
            self._last_error = error
        if old_state != new_state:
            ConsoleLogger.write(
                f"[{self.slot_id}] State: {old_state.name} -> {new_state.name}",
                LogLevel.INFO,
            )
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
                    ConsoleLogger.write(
                        f"[{self.slot_id}] state cb error: {e}",
                        LogLevel.ERROR,
                    )

    # ── Обработчики событий лог-вотчера ───────────────────────
    async def _on_reboot_line(self, line: str) -> None:
        self.on_reboot_detected()

    async def _on_shutdown_line(self, line: str) -> None:
        self.on_shutdown_detected()

    async def _on_power_on_line(self, line: str) -> None:
        self.on_power_on_detected()

    async def _on_login_prompt_line(self, line: str) -> None:
        self.on_login_prompt_detected()

    # ── Реакция на события платы ──────────────────────────────
    def on_reboot_detected(self):
        """Обнаружена перезагрузка платы."""
        self.set_state(SlotState.REBOOTING)
        self.boot_automation.reset()
        if self.board:
            self.board.reset()

    def on_shutdown_detected(self):
        """Обнаружено выключение платы."""
        self.set_state(SlotState.SHUTDOWN)
        if self.board:
            self.board.reset()

    def on_power_on_detected(self):
        """Обнаружено включение платы (начало загрузки)."""
        if self._state == SlotState.REBOOTING:
            self.set_state(SlotState.STARTING)
        if self.board:
            self.board.bios = True
            self.board.touch()

    def on_login_prompt_detected(self):
        """Обнаружено приглашение логина — ОС загрузилась."""
        if self.board:
            self.board.os = True
            self.board.touch()

    def on_login_success(self):
        """Обнаружен успешный логин в шелл."""
        if self.board:
            self.board.os = True
            self.board.shell = True
            self.board.touch()
        if self._state in (SlotState.CONNECTING, SlotState.REBOOTING):
            self.set_state(SlotState.READY)

    # ── Мониторинг логина ─────────────────────────────────────
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
        """Ждёт подключения каналов. Переводит в READY, когда хотя бы один жив."""
        while self._started:
            if self._state in (SlotState.STARTING, SlotState.REBOOTING):
                if self.ws_channel.is_alive or self.serial_channel.is_alive:
                    self.set_state(SlotState.READY)
                    return
            await asyncio.sleep(0.5)

    # ── Обновление датчиков и метрик ──────────────────────────
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

    # ── Lifecycle ─────────────────────────────────────────────
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
                ConsoleLogger.write(
                    f"[{self.slot_id}] WS channel failed to start: {e}",
                    LogLevel.WARNING,
                )
            try:
                await self.serial_channel.start()
            except Exception as e:
                ConsoleLogger.write(
                    f"[{self.slot_id}] Serial channel failed to start: {e}",
                    LogLevel.WARNING,
                )
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

            # ✅ Запускаем фоновый мониторинг каналов
            # Он переведёт в READY, когда клиент подключится по WS
            self._channel_watcher = asyncio.create_task(self._watch_channels())

        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            self._started = False
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
            self._boot_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await self._boot_watcher
            self._boot_watcher = None

        # Разбудить все ожидающие задачи
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(
                    ChannelDead(f"[{self.slot_id}] slot stopped")
                )
        self._pending.clear()

        # Отменить насосы
        for t in self._pumps:
            t.cancel()
        for t in self._pumps:
            with suppress(asyncio.CancelledError):
                await t
        self._pumps.clear()

        # Остановить каналы в обратном порядке
        try:
            await self.log_watcher.stop()
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.slot_id}] log_watcher stop error: {e}", LogLevel.ERROR
            )
        try:
            await self.serial_channel.stop()
            self.boot_automation.is_logged_in = False
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.slot_id}] serial stop error: {e}", LogLevel.ERROR
            )
        try:
            await self.ws_channel.stop()
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.slot_id}] ws stop error: {e}", LogLevel.ERROR
            )

        await self.power_control.power_off()
        self.set_state(SlotState.IDLE)

        # Сбросить плату
        if self.board:
            self.board.reset()

    # ── Публичное API: выполнение задачи ─────────────────────
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

    # ── Маршрутизация (фейловер) ──────────────────────────────
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
                ConsoleLogger.write(
                    f"[{self.slot_id}] task {record.task_id} -> {ch.name}",
                    LogLevel.INFO,
                )
            except ChannelDead as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.slot_id}] send {ch.name} fail: {e}",
                    LogLevel.WARNING,
                )
                continue
            try:
                report = await self._wait_report(record, ch)
                ConsoleLogger.write(
                    f"[{self.slot_id}] task {record.task_id} <- {ch.name} "
                    f"({report.get('status')})",
                    LogLevel.INFO,
                )
                return report
            except asyncio.TimeoutError as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.slot_id}] {ch.name} timeout по {record.task_id}, "
                    f"пробую резерв",
                    LogLevel.WARNING,
                )
                continue
            except ChannelDead as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.slot_id}] {ch.name} умер в процессе "
                    f"{record.task_id}, пробую резерв",
                    LogLevel.WARNING,
                )
                continue

        if not any(ch.is_alive for ch in self.channels):
            self.set_state(SlotState.OFFLINE, "All channels failed")
        raise AllChannelsFailed(self.slot_id, record.task_id, tried, last_err)

    # ── Насос: чтение отчётов из каналов ──────────────────────
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
            ConsoleLogger.write(
                f"[{self.slot_id}] pump {ch.name} упал: {e}",
                LogLevel.ERROR,
            )

    # ── Информация для UI ─────────────────────────────────────
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