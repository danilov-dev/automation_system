"""
Slot — полноценная сущность тестируемого ПК.

Инкапсулирует:
  - Все компоненты (TCP, Serial, LogWatcher, BootAutomation)
  - Логику маршрутизации задач и failover (бывший PcSession)
  - Состояние слота и переходы между состояниями
  - Единый интерфейс для внешнего мира

Не знает:
  - про TcpRouter (роутер регистрирует каналы сам)
  - про TUI / FastAPI / оркестратор
"""
import asyncio
import uuid
from contextlib import suppress
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from app.channels.base import Channel, ChannelDead, ChannelError
from app.channels.serial_channel import SerialChannel
from app.channels.tcp import TcpChannel
from app.log_watcher import LogWatcher
from app.boot_automation import BootAutomation
from app.utils.console_logger import ConsoleLogger, LogLevel


# ═══════════════════════════════════════════════════════════════
#  Исключения
# ═══════════════════════════════════════════════════════════════
class AllChannelsFailed(ChannelError):
    """Ни один канал не смог доставить задачу и получить отчёт."""

    def __init__(
        self,
        pc_id: str,
        task_id: str,
        tried: list[str],
        last_err: Exception | None,
    ):
        msg = (
            f"[{pc_id}] task {task_id} не выполнен. "
            f"tried={tried}, last_err={last_err!r}"
        )
        super().__init__(msg)
        self.pc_id = pc_id
        self.task_id = task_id
        self.tried = tried
        self.last_err = last_err


# ═══════════════════════════════════════════════════════════════
#  Внутренние классы
# ═══════════════════════════════════════════════════════════════
class _PendingTask:
    """
    Состояние ожидания одного отчёта.
    final    — Future с финальным отчётом (report|error, не in_progress).
    progress — Event, взводится при 'in_progress'; продлевает окно ожидания.
    """

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
#  Состояния слота
# ═══════════════════════════════════════════════════════════════
class SlotState(Enum):
    IDLE = auto()          # Инициализирован, но не запущен
    STARTING = auto()      # Запускается
    CONNECTING = auto()    # Ждёт подключения клиента (TCP handshake)
    READY = auto()         # Готов к работе
    TESTING = auto()       # Выполняет тест
    REBOOTING = auto()     # Перезагружается
    SHUTDOWN = auto()      # Выключается
    ERROR = auto()         # Ошибка
    OFFLINE = auto()       # Каналы недоступны


# ═══════════════════════════════════════════════════════════════
#  Slot
# ═══════════════════════════════════════════════════════════════
class Slot:
    """
    Полноценная сущность слота.
    Держит два канала (primary=TCP, backup=Serial), роутит задачи и отчёты,
    делает failover с сохранением task_id.
    """

    def __init__(
        self,
        pc_id: str,
        tcp_channel: TcpChannel,
        serial_channel: SerialChannel,
        log_watcher: LogWatcher,
        boot_automation: BootAutomation,
    ):
        self.pc_id = pc_id
        self.tcp_channel = tcp_channel
        self.serial_channel = serial_channel
        self.log_watcher = log_watcher
        self.boot_automation = boot_automation

        self._state = SlotState.IDLE
        self._state_changed_at: Optional[datetime] = None
        self._last_error: Optional[str] = None

        self._pending: dict[str, _PendingTask] = {}
        self._pumps: list[asyncio.Task] = []
        self._started = False

    # ── Свойства состояния ──────────────────────────────────

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

    # ── Статусы каналов ────────────────────────────────────

    @property
    def tcp_alive(self) -> bool:
        return self.tcp_channel.is_alive

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
        return [self.tcp_channel, self.serial_channel]

    # ── Управление состоянием ───────────────────────────────

    def set_state(self, new_state: SlotState, error: Optional[str] = None):
        old_state = self._state
        self._state = new_state
        self._state_changed_at = datetime.now()
        if error:
            self._last_error = error
        if old_state != new_state:
            ConsoleLogger.write(
                f"[{self.pc_id}] State: {old_state.name} → {new_state.name}",
                LogLevel.INFO,
            )

    # ─ Lifecycle ───────────────────────────────────────────

    async def start(self):
        """Запустить все компоненты слота и pump'ы."""
        if self._started:
            return
        self.set_state(SlotState.STARTING)
        self._started = True

        try:
            # Запускаем каналы (TcpChannel.start() только помечает started)
            await self.tcp_channel.start()
            await self.serial_channel.start()
            await self.log_watcher.start()

            # Запускаем pump'ы для чтения отчётов из каналов
            self._pumps = [
                asyncio.create_task(
                    self._pump(ch), name=f"pump-{self.pc_id}-{ch.name}"
                )
                for ch in self.channels
            ]

            self.set_state(SlotState.CONNECTING)

            # Проверяем доступность каналов
            if self.tcp_channel.is_alive:
                self.set_state(SlotState.READY)
            elif self.serial_channel.is_alive:
                self.set_state(SlotState.READY)
            else:
                self.set_state(SlotState.OFFLINE, "No channels available")

        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            self._started = False
            raise

    async def stop(self):
        """Остановить все компоненты слота."""
        if not self._started:
            return
        self._started = False

        # Разбудить все ожидающие задачи
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(
                    ChannelDead(f"[{self.pc_id}] slot stopped")
                )
        self._pending.clear()

        # Отменить pump'ы
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
                f"[{self.pc_id}] log_watcher stop error: {e}", LogLevel.ERROR
            )
        try:
            await self.serial_channel.stop()
            self.boot_automation.is_logged_in = False
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.pc_id}] serial stop error: {e}", LogLevel.ERROR
            )
        try:
            await self.tcp_channel.stop()
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.pc_id}] tcp stop error: {e}", LogLevel.ERROR
            )

        self.set_state(SlotState.IDLE)

    # ── Публичное API: выполнение задачи ────────────────────

    async def execute(
        self,
        command: str,
        params: dict | None = None,
        progress_timeout: float = 15.0,
    ) -> dict:
        """
        Отправить задачу и дождаться отчёта.
        progress_timeout — окно между 'in_progress'-сигналами.
        Общий жёсткий таймаут задаётся снаружи через asyncio.wait_for(...).
        """
        if not self.is_ready:
            raise RuntimeError(
                f"Slot {self.pc_id} is not ready (state: {self._state.name})"
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
            return await self._route(payload, record)
        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            raise
        finally:
            self._pending.pop(task_id, None)
            if self._state == SlotState.TESTING:
                self.set_state(SlotState.READY)

    # ── Маршрутизация (failover) ────────────────────────────

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
                    f"[{self.pc_id}] task {record.task_id} → {ch.name}",
                    LogLevel.INFO,
                )
            except ChannelDead as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.pc_id}] send {ch.name} fail: {e}",
                    LogLevel.WARNING,
                )
                continue

            try:
                report = await self._wait_report(record, ch)
                ConsoleLogger.write(
                    f"[{self.pc_id}] task {record.task_id} ← {ch.name} "
                    f"({report.get('status')})",
                    LogLevel.INFO,
                )
                return report
            except asyncio.TimeoutError as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.pc_id}] {ch.name} timeout по {record.task_id}, "
                    f"пробую резерв",
                    LogLevel.WARNING,
                )
                continue
            except ChannelDead as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.pc_id}] {ch.name} умер в процессе "
                    f"{record.task_id}, пробую резерв",
                    LogLevel.WARNING,
                )
                continue

        raise AllChannelsFailed(self.pc_id, record.task_id, tried, last_err)

    async def _wait_report(self, record: _PendingTask, ch: Channel) -> dict:
        """
        Ждём финальный отчёт. Каждый in_progress продлевает окно.
        Если канал умер — ChannelDead, вызывающий пойдёт на резерв.
        """
        while True:
            record.progress.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.final),
                    timeout=record.progress_timeout,
                )
            except asyncio.TimeoutError:
                # Сначала проверяем живость канала, потом прогресс
                if not ch.is_alive:
                    raise ChannelDead(
                        f"{ch.name} died while waiting {record.task_id}"
                    )
                if not record.progress.is_set():
                    raise
                continue
            return record.final.result()

    # ─ Pump: чтение отчётов из каналов ─────────────────────

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
                f"[{self.pc_id}] pump {ch.name} упал: {e}",
                LogLevel.ERROR,
            )

    # ── Обработка событий COM1 ──────────────────────────────

    def on_reboot_detected(self):
        self.set_state(SlotState.REBOOTING)
        self.boot_automation.reset()

    def on_shutdown_detected(self):
        self.set_state(SlotState.SHUTDOWN)

    def on_power_on_detected(self):
        pass

    def on_login_success(self):
        if self._state in (SlotState.CONNECTING, SlotState.REBOOTING):
            self.set_state(SlotState.READY)

    # ── Информация для UI ───────────────────────────────────

    def get_status_dict(self) -> dict:
        return {
            "pc_id": self.pc_id,
            "state": self._state.name,
            "state_duration": self.state_duration,
            "tcp_alive": self.tcp_alive,
            "serial_alive": self.serial_alive,
            "log_watcher_alive": self.log_watcher_alive,
            "is_logged_in": self.is_logged_in,
            "last_error": self._last_error,
            "last_log_lines": list(self.log_watcher.lines)[-5:],
        }