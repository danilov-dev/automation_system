# app/slot.py

from enum import Enum, auto
from typing import Optional
from datetime import datetime

from app.channels.tcp import TcpChannel
from app.channels.serial_channel import SerialChannel
from app.log_watcher import LogWatcher
from app.boot_automation import BootAutomation
from app.pc_session import PcSession


class SlotState(Enum):
    """Состояния слота."""
    IDLE = auto()  # Инициализирован, но не запущен
    STARTING = auto()  # Запускается
    CONNECTING = auto()  # Ждёт подключения клиента (TCP handshake)
    READY = auto()  # Готов к работе (TCP подключён, залогинен)
    TESTING = auto()  # Выполняет тест
    REBOOTING = auto()  # Перезагружается
    SHUTDOWN = auto()  # Выключается
    ERROR = auto()  # Ошибка
    OFFLINE = auto()  # Каналы недоступны


class Slot:
    """
    Полноценная сущность слота.

    Инкапсулирует:
    - Все компоненты (TCP, Serial, LogWatcher, BootAutomation, PcSession)
    - Состояние слота
    - Логику переходов состояний
    - Единый интерфейс для внешнего мира
    """

    def __init__(
            self,
            pc_id: str,
            tcp_channel: TcpChannel,
            serial_channel: SerialChannel,
            log_watcher: LogWatcher,
            boot_automation: BootAutomation,
            pc_session: PcSession,
    ):
        self.pc_id = pc_id
        self.tcp_channel = tcp_channel
        self.serial_channel = serial_channel
        self.log_watcher = log_watcher
        self.boot_automation = boot_automation
        self.pc_session = pc_session

        self._state = SlotState.IDLE
        self._state_changed_at: Optional[datetime] = None
        self._last_error: Optional[str] = None

    # ── Свойства состояния ──────────────────────────────────

    @property
    def state(self) -> SlotState:
        return self._state

    @property
    def state_duration(self) -> Optional[float]:
        """Сколько секунд слот в текущем состоянии."""
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

    # ── Управление состоянием ───────────────────────────────

    def set_state(self, new_state: SlotState, error: Optional[str] = None):
        """Изменить состояние слота."""
        old_state = self._state
        self._state = new_state
        self._state_changed_at = datetime.now()

        if error:
            self._last_error = error

        # Логирование перехода
        if old_state != new_state:
            from app.utils.console_logger import ConsoleLogger, LogLevel
            ConsoleLogger.write(
                f"[{self.pc_id}] State: {old_state.name} → {new_state.name}",
                LogLevel.INFO,
            )

    # ── Бизнес-логика ───────────────────────────────────────

    async def start(self):
        """Запустить все компоненты слота."""
        self.set_state(SlotState.STARTING)

        try:
            # Запускаем каналы
            await self.tcp_channel.start()
            await self.serial_channel.start()
            await self.log_watcher.start()

            # Ждём подключения TCP (handshake)
            self.set_state(SlotState.CONNECTING)

            # Проверяем, что TCP подключился
            if self.tcp_channel.is_alive:
                self.set_state(SlotState.READY)
            else:
                # TCP не подключился, но Serial может быть жив
                if self.serial_channel.is_alive:
                    self.set_state(SlotState.READY)
                else:
                    self.set_state(SlotState.OFFLINE, "No channels available")
        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            raise

    async def stop(self):
        """Остановить все компоненты слота."""
        try:
            await self.log_watcher.stop()
            await self.serial_channel.stop()
            await self.tcp_channel.stop()
            self.set_state(SlotState.IDLE)
        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            raise

    async def execute(self, command: str, params: dict = None, timeout: float = 30.0) -> dict:
        """Выполнить команду на слоте."""
        if not self.is_ready:
            raise RuntimeError(f"Slot {self.pc_id} is not ready (state: {self._state.name})")

        self.set_state(SlotState.TESTING)

        try:
            result = await self.pc_session.execute(command, params, progress_timeout=timeout)
            self.set_state(SlotState.READY)
            return result
        except Exception as e:
            self.set_state(SlotState.ERROR, str(e))
            raise

    # ── Обработка событий ───────────────────────────────────

    def on_reboot_detected(self):
        """Вызывается при обнаружении reboot в логах."""
        self.set_state(SlotState.REBOOTING)
        self.boot_automation.reset()

    def on_shutdown_detected(self):
        """Вызывается при обнаружении shutdown в логах."""
        self.set_state(SlotState.SHUTDOWN)

    def on_power_on_detected(self):
        """Вызывается при обнаружении power on в логах."""
        # После power on ждём загрузку и автологин
        pass

    def on_login_success(self):
        """Вызывается при успешном автологине."""
        if self._state in (SlotState.CONNECTING, SlotState.REBOOTING):
            self.set_state(SlotState.READY)

    # ── Информация для UI ───────────────────────────────────

    def get_status_dict(self) -> dict:
        """Возвращает полный статус слота для UI."""
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