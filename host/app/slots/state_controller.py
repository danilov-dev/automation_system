"""
SlotStateController — единая точка управления состоянием слота (SSOT).
Единственный класс, который имеет право менять состояние слота.
Отвечает за:
— управление состоянием слота (конечный автомат SlotState)
— строгую матрицу допустимых переходов между состояниями
— управление состоянием платы (Board: bios, os, shell)
— метрики питания (v, a, wt) и прогресс теста (step, test_time)
— обработку семантических событий платы (reboot, shutdown, power_on, login)
— уведомление подписчиков о смене состояния
— управление сессией тестирования (on_session_started/finished)
НЕ отвечает за:
— наблюдение за каналами и загрузкой (это SlotMonitor)
— выполнение задач (это Slot + TaskRouter + SlotTestRunner)
— работу с каналами напрямую (это Slot)
ВАЖНО: все методы, начинающиеся с on_*, — это ЕДИНСТВЕННАЯ точка входа
для смены состояния. Никто не должен вызывать set_state() напрямую.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, Union
from host.app.slots.state import SlotState
from host.app.board import Board
from host.app.boot_automation import BootAutomation

# callback(slot_id, old_state_name, new_state_name)
StateCallback = Callable[[str, str, str], Awaitable[None]]
# callback(slot_id, v, a, wt)
MetricsCallback = Callable[[str, float, float, float], Awaitable[None]]
# callback(slot_id, bios, os, shell)
BoardCallback = Callable[[str, bool, bool, bool], Awaitable[None]]
# callback(slot_id, step, test_time)
ProgressCallback = Callable[[str, int, timedelta], Awaitable[None]]
# callback(slot_id, ws_alive, serial_alive, log_alive)
ChannelsCallback = Callable[[str, bool, bool, bool], Union[None, Awaitable[None]]]

# Матрица допустимых переходов.
VALID_TRANSITIONS: dict[SlotState, set[SlotState]] = {
    SlotState.IDLE: {SlotState.STARTING},
    SlotState.STARTING: {SlotState.CONNECTING, SlotState.REBOOTING, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.CONNECTING: {SlotState.READY, SlotState.REBOOTING, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.READY: {SlotState.TESTING, SlotState.REBOOTING, SlotState.SHUTDOWN, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.TESTING: {SlotState.READY, SlotState.REBOOTING, SlotState.SHUTDOWN, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.REBOOTING: {SlotState.CONNECTING, SlotState.READY, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.SHUTDOWN: {SlotState.OFFLINE, SlotState.ERROR, SlotState.IDLE},
    SlotState.ERROR: {SlotState.IDLE, SlotState.OFFLINE, SlotState.STARTING, SlotState.REBOOTING},
    SlotState.OFFLINE: {SlotState.STARTING, SlotState.IDLE, SlotState.ERROR},
}


class SlotStateController:
    """Управление состоянием слота, платой и метриками."""

    def __init__(
            self,
            slot_id: str,
            boot_automation: BootAutomation,
    ):
        self.slot_id = slot_id
        self.boot_automation = boot_automation

        # ── Состояние слота ───────────────────────────────
        self._state = SlotState.IDLE
        self._state_changed_at: Optional[datetime] = None
        self._last_error: Optional[str] = None

        # ── Плата и её состояние ──────────────────────────
        self.board: Optional[Board] = None

        # ── Метрики питания ──
        self.v: float = 0.0
        self.a: float = 0.0
        self.wt: float = 0.0

        # ── Прогресс текущего теста ───────────────────────
        self.step: int = 0
        self.test_time: timedelta = timedelta()
        self.current_command: Optional[str] = None

        # ── Сессия тестирования ───────────────────────────
        self.session_total: int = 0
        self.session_done: int = 0
        self.session_started_at: Optional[datetime] = None

        # ── Подписчики ────────────────────────────────────
        self._state_callbacks: list[StateCallback] = []
        self._metrics_callbacks: list[MetricsCallback] = []
        self._board_callbacks: list[BoardCallback] = []
        self._progress_callbacks: list[ProgressCallback] = []
        self._channels_callbacks: list[ChannelsCallback] = []

        # ── Кэш живости каналов ──
        self._ws_alive: bool = False
        self._serial_alive: bool = False
        self._log_alive: bool = False

        # ── Logger ───────────────────────────────────────
        self.logger = logging.getLogger(f"slot_{slot_id}")

    # ── Свойства состояния ────────────────────────────────
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

    @property
    def ws_alive(self) -> bool:
        return self._ws_alive

    @property
    def serial_alive(self) -> bool:
        return self._serial_alive

    @property
    def log_alive(self) -> bool:
        return self._log_alive

    # ── Подписка на изменения ─────────────────────────────
    def on_state_change(self, cb: StateCallback) -> None:
        self._state_callbacks.append(cb)

    def on_metrics_change(self, cb: MetricsCallback) -> None:
        self._metrics_callbacks.append(cb)

    def on_board_change(self, cb: BoardCallback) -> None:
        self._board_callbacks.append(cb)

    def on_channels_change(self, cb: ChannelsCallback) -> None:
        self._channels_callbacks.append(cb)

    def on_progress_change(self, cb: ProgressCallback) -> None:
        self._progress_callbacks.append(cb)

    # ── Вспомогательные методы ────────────────────────────
    async def _safe_call(self, cb: Callable, *args) -> None:
        try:
            await cb(*args)
        except Exception as e:
            self.logger.error(f"callback error: {e}")

    def _emit(self, callbacks: list, *args) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.logger.error("No running event loop for callbacks")
            return
        for cb in callbacks:
            loop.create_task(self._safe_call(cb, *args))

    # ── Приватный метод смены состояния ───────────────────
    def _transition(self, new_state: SlotState, error: Optional[str] = None) -> bool:
        old_state = self._state
        if old_state == new_state:
            return False
        allowed = VALID_TRANSITIONS.get(old_state, set())
        if new_state not in allowed:
            self.logger.warning(
                f"Transition REJECTED: {old_state.name} -> {new_state.name} "
                f"(not allowed). Error: {error}"
            )
            return False
        self._state = new_state
        self._state_changed_at = datetime.now()
        if error:
            self._last_error = error
        self.logger.info(f"State: {old_state.name} -> {new_state.name} | error={error}")
        self._emit(self._state_callbacks, self.slot_id, old_state.name, new_state.name)
        return True

    # ── Сессия тестирования ───────────────────────────────
    def on_session_started(self, total: int) -> None:
        """Началась сессия тестирования."""
        self.session_total = total
        self.session_done = 0
        self.session_started_at = datetime.now()
        self.step = 0
        self._transition(SlotState.TESTING)
        self.logger.info(f"Session started: {total} tests")

    def on_session_finished(self) -> None:
        """Сессия завершена — слот снова готов."""
        self.current_command = None
        self.session_started_at = None
        if self._state == SlotState.TESTING:
            self._transition(SlotState.READY)
        self.notify_progress()
        self.logger.info(f"Session finished: {self.session_done}/{self.session_total}")

    # ── Задачи ────────────────────────────────────────────
    def on_task_started(self, command: str) -> None:
        """Начался конкретный тест внутри сессии."""
        self.current_command = command
        self.step = 0
        if self._state == SlotState.READY:
            self._transition(SlotState.TESTING)
        self.notify_progress()

    def on_task_finished(self) -> None:
        """Тест завершён. В READY возвращаемся только по концу сессии."""
        self.session_done += 1
        self.current_command = None
        self.notify_progress()

    def on_task_failed(self, error: str) -> None:
        """Ошибка теста. Это не всегда фатально для слота."""
        self._last_error = error
        self.logger.warning(f"task failed: {error}")

    # ── Прогресс ──────────────────────────────────────────
    def apply_progress(self, msg: dict) -> None:
        """Промежуточный отчёт: обновляем step и реальное время."""
        result = msg.get("result") or msg
        if isinstance(result, dict) and "step" in result:
            try:
                self.step = int(result["step"])
            except (TypeError, ValueError):
                return
        if self.session_started_at:
            self.test_time = datetime.now() - self.session_started_at
        self.notify_progress()

    # ── Семантические методы ──────────────────────────────
    def on_start_requested(self) -> None:
        self._transition(SlotState.STARTING)

    def on_channels_established(self) -> None:
        self._transition(SlotState.CONNECTING)

    def on_login_success(self) -> None:
        if self.board:
            self.board.os = True
            self.board.shell = True
            self.board.touch()
            self.notify_board()
        if self._state in (SlotState.STARTING, SlotState.CONNECTING, SlotState.REBOOTING):
            self._transition(SlotState.READY)
        self.logger.debug("login success detected")

    def on_power_lost(self, reason: str) -> None:
        if self._transition(SlotState.OFFLINE, reason):
            self.boot_automation.reset()
            if self.board:
                self.board.reset()
                self.notify_board()
            self.logger.warning(f"Power lost: {reason}")

    def on_power_restored(self) -> None:
        if self._state == SlotState.OFFLINE:
            self._transition(SlotState.STARTING)
            self.logger.info("Power restored, starting boot cycle")

    def on_reboot_detected(self) -> None:
        if self._transition(SlotState.REBOOTING):
            self.boot_automation.reset()
            if self.board:
                self.board.reset()
                self.notify_board()
            self.logger.debug("reboot detected")

    def on_shutdown_detected(self) -> None:
        if self._transition(SlotState.SHUTDOWN):
            if self.board:
                self.board.reset()
                self.notify_board()
            self.logger.debug("shutdown detected")

    def on_power_on_detected(self) -> None:
        if self._state == SlotState.REBOOTING:
            self._transition(SlotState.CONNECTING)
        if self.board:
            self.board.bios = True
            self.board.touch()
            self.notify_board()
        self.logger.debug("Board power-on detected")

    def on_login_prompt_detected(self) -> None:
        if self.board:
            self.board.os = True
            self.board.touch()
            self.notify_board()
        self.logger.debug("login prompt detected")

    def on_board_alive(self) -> None:
        if not self.board:
            return
        changed = False
        if not self.board.bios:
            self.board.bios = True
            changed = True
        if not self.board.os:
            self.board.os = True
            changed = True
        if changed:
            self.board.touch()
            self.notify_board()
            self.logger.debug("Board alive: bios/os marked as active")

    def on_fatal_error(self, error: str) -> None:
        self._transition(SlotState.ERROR, error)

    def on_slot_stopped(self) -> None:
        self._transition(SlotState.IDLE)
        if self.board:
            self.board.reset()
            self.notify_board()

    def on_channels_status_changed(self, ws: bool, serial: bool, log: bool) -> None:
        if (ws, serial, log) == (self._ws_alive, self._serial_alive, self._log_alive):
            return
        self._ws_alive, self._serial_alive, self._log_alive = ws, serial, log
        self.notify_channels()
        self.logger.debug(f"Channels status: ws={ws} serial={serial} log={log}")

    def notify_channels(self) -> None:
        for cb in self._channels_callbacks:
            try:
                res = cb(self.slot_id, self._ws_alive, self._serial_alive, self._log_alive)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass
            except Exception as e:
                self.logger.error(f"channels cb error: {e}")

    # ── Метрики и отчёты ──────────────────────────────────
    def notify_update_metrics(self, v: float, a: float, wt: float) -> None:
        if (self.v, self.a, self.wt) == (v, a, wt):
            return
        self.v, self.a, self.wt = v, a, wt
        self._emit(self._metrics_callbacks, self.slot_id, v, a, wt)

    def notify_board(self) -> None:
        if not self.board:
            return
        self._emit(
            self._board_callbacks,
            self.slot_id,
            self.board.bios,
            self.board.os,
            self.board.shell,
        )

    def notify_progress(self) -> None:
        self._emit(self._progress_callbacks, self.slot_id, self.step, self.test_time)

    def apply_report(self, report: dict) -> None:
        """Применить данные из финального отчёта."""
        result = report.get("result", {})
        if not isinstance(result, dict):
            return
        if "step" in result:
            self.step = int(result["step"])
        if self.session_started_at:
            self.test_time = datetime.now() - self.session_started_at

    # ── Информация для UI ─────────────────────────────────
    def get_status_dict(
            self,
            ws_alive: bool,
            serial_alive: bool,
            log_watcher_alive: bool,
            is_logged_in: bool,
            last_log_lines: list[str],
    ) -> dict:
        return {
            "slot_id": self.slot_id,
            "state": self._state.name,
            "state_duration": self.state_duration,
            "ws_alive": ws_alive,
            "serial_alive": serial_alive,
            "log_watcher_alive": log_watcher_alive,
            "is_logged_in": is_logged_in,
            "last_error": self._last_error,
            "v": self.v,
            "a": self.a,
            "wt": self.wt,
            "step": self.step,
            "test_time": self.test_time.total_seconds(),
            "current_command": self.current_command,
            "session_total": self.session_total,
            "session_done": self.session_done,
            "last_log_lines": last_log_lines,
        }