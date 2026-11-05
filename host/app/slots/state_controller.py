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
НЕ отвечает за:
— наблюдение за каналами и загрузкой (это SlotMonitor)
— выполнение задач (это Slot + TaskRouter)
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
# Если перехода нет в этом словаре — он будет отклонён.
# Это защищает от случайных прыжков между состояниями и гонок.
VALID_TRANSITIONS: dict[SlotState, set[SlotState]] = {
    SlotState.IDLE: {SlotState.STARTING},
    SlotState.STARTING: {SlotState.CONNECTING, SlotState.REBOOTING, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.CONNECTING: {SlotState.READY, SlotState.REBOOTING, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.READY: {SlotState.TESTING, SlotState.REBOOTING, SlotState.SHUTDOWN, SlotState.ERROR, SlotState.OFFLINE,
                      SlotState.IDLE},
    SlotState.TESTING: {SlotState.READY, SlotState.REBOOTING, SlotState.SHUTDOWN, SlotState.ERROR, SlotState.OFFLINE,
                        SlotState.IDLE},
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

        # ── Метрики питания (пока заглушки, будут драйвером) ──
        self.v: float = 0.0
        self.a: float = 0.0
        self.wt: float = 0.0

        # ── Прогресс текущего теста ───────────────────────
        self.step: int = 0
        self.test_time: timedelta = timedelta()

        # ── Подписчики (все колбэки асинхронные) ─────────
        self._state_callbacks: list[StateCallback] = []
        self._metrics_callbacks: list[MetricsCallback] = []
        self._board_callbacks: list[BoardCallback] = []
        self._progress_callbacks: list[ProgressCallback] = []
        self._channels_callbacks: list[ChannelsCallback] = []

        # ── Кэш живости каналов (обновляется монитором) ──
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
        """Зарегистрировать асинхронный колбэк смены состояния слота."""
        self._state_callbacks.append(cb)

    def on_metrics_change(self, cb: MetricsCallback) -> None:
        """Зарегистрировать асинхронный колбэк смены метрик тока."""
        self._metrics_callbacks.append(cb)

    def on_board_change(self, cb: BoardCallback) -> None:
        """Зарегистрировать асинхронный колбэк смены состояния платы."""
        self._board_callbacks.append(cb)

    def on_channels_change(self, cb: ChannelsCallback) -> None:
        """Зарегистрировать колбэк смены живости каналов."""
        self._channels_callbacks.append(cb)

    def on_progress_change(self, cb: ProgressCallback) -> None:
        """Зарегистрировать асинхронный колбэк смены прогресса теста."""
        self._progress_callbacks.append(cb)

    # ── Вспомогательные методы для запуска асинхронных колбэков ──
    async def _safe_call(self, cb: Callable, *args) -> None:
        """Безопасный вызов асинхронного колбэка с логированием ошибок."""
        try:
            await cb(*args)
        except Exception as e:
            self.logger.error(f"callback error: {e}")

    def _emit(self, callbacks: list, *args) -> None:
        """
        Запускает асинхронные колбэки в текущем event loop.
        Вызывается из синхронных методов, но требует запущенного цикла.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.logger.error("No running event loop for callbacks")
            return
        for cb in callbacks:
            loop.create_task(self._safe_call(cb, *args))

    # ── Приватный метод смены состояния (ЕДИНСТВЕННАЯ точка) ──
    def _transition(self, new_state: SlotState, error: Optional[str] = None) -> bool:
        """
        Попытаться перейти в новое состояние.
        Проверяет матрицу допустимых переходов.
        Возвращает True, если переход выполнен, False — если отклонён.
        """
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

        # Уведомляем подписчиков (асинхронно)
        self._emit(self._state_callbacks, self.slot_id, old_state.name, new_state.name)

        return True

    # ── Семантические методы (Единственная публичная точка входа) ──
    def on_start_requested(self) -> None:
        """Запрошен запуск слота (первый старт или восстановление после ошибки)."""
        self._transition(SlotState.STARTING)

    def on_channels_established(self) -> None:
        """Каналы (WS, Serial, LogWatcher) успешно запущены."""
        self._transition(SlotState.CONNECTING)

    def on_login_success(self) -> None:
        """Обнаружен успешный логин в шелл — плата готова к работе."""
        if self.board:
            self.board.os = True
            self.board.shell = True
            self.board.touch()
            self.notify_board()  # ← Уведомляем подписчиков
        if self._state in (SlotState.STARTING, SlotState.CONNECTING, SlotState.REBOOTING):
            self._transition(SlotState.READY)
        self.logger.debug("login success detected")

    def on_task_started(self) -> None:
        """Задача отправлена на плату."""
        self._transition(SlotState.TESTING)

    def on_task_finished(self) -> None:
        """Задача выполнена, плата снова готова."""
        self._transition(SlotState.READY)

    def on_power_lost(self, reason: str) -> None:
        """
        Обнаружен обрыв питания.
        Всегда переходим в OFFLINE, что бы ни происходило.
        Сбрасываем состояние платы и авторизации.
        """
        if self._transition(SlotState.OFFLINE, reason):
            self.boot_automation.reset()
            if self.board:
                self.board.reset()
                self.notify_board()
            self.logger.warning(f"Power lost: {reason}")

    def on_power_restored(self) -> None:
        """
        Обнаружено восстановление питания.
        Переходим в STARTING, чтобы заново пройти цикл загрузки.
        Slot (оркестратор) перехватит это состояние и перезапустит каналы.
        """
        if self._state == SlotState.OFFLINE:
            self._transition(SlotState.STARTING)
            self.logger.info("Power restored, starting boot cycle")

    def on_reboot_detected(self) -> None:
        """Обнаружена перезагрузка платы (по логам COM)."""
        if self._transition(SlotState.REBOOTING):
            self.boot_automation.reset()
            if self.board:
                self.board.reset()
                self.notify_board()
            self.logger.debug("reboot detected")

    def on_shutdown_detected(self) -> None:
        """Обнаружено выключение платы (по логам COM)."""
        if self._transition(SlotState.SHUTDOWN):
            if self.board:
                self.board.reset()
                self.notify_board()
            self.logger.debug("shutdown detected")

    def on_power_on_detected(self) -> None:
        """Обнаружено включение платы (начало загрузки, по логам COM)."""
        if self._state == SlotState.REBOOTING:
            self._transition(SlotState.CONNECTING)
        if self.board:
            self.board.bios = True
            self.board.touch()
            self.notify_board()
        self.logger.debug("Board power-on detected")

    def on_login_prompt_detected(self) -> None:
        """Обнаружено приглашение логина — ОС загрузилась."""
        if self.board:
            self.board.os = True
            self.board.touch()
            self.notify_board()
        self.logger.debug("login prompt detected")

    def on_board_alive(self) -> None:
        """
        Плата обнаружена (WS жив, BIOS/OS активны).
        Вызывается монитором, когда WS-канал жив и плата существует.
        Это семантический метод — только контроллер имеет право менять board.
        """
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
        """Критическая ошибка, слот не может работать."""
        self._transition(SlotState.ERROR, error)

    def on_slot_stopped(self) -> None:
        """Слот полностью остановлен (после вызова Slot.stop())."""
        self._transition(SlotState.IDLE)
        if self.board:
            self.board.reset()
            self.notify_board()

    def on_channels_status_changed(self, ws: bool, serial: bool, log: bool) -> None:
        """
        Семантический метод: монитор сообщил об изменении живости каналов.
        Только контроллер хранит и раздаёт живость каналов.
        """
        if (ws, serial, log) == (self._ws_alive, self._serial_alive, self._log_alive):
            return
        self._ws_alive, self._serial_alive, self._log_alive = ws, serial, log
        self.notify_channels()
        self.logger.debug(f"Channels status: ws={ws} serial={serial} log={log}")

    def notify_channels(self) -> None:
        """Уведомить подписчиков о живости каналов."""
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
        """Обновить показания датчиков питания и уведомить подписчиков."""
        if (self.v, self.a, self.wt) == (v, a, wt):
            return
        self.v, self.a, self.wt = v, a, wt
        self._emit(self._metrics_callbacks, self.slot_id, v, a, wt)

    def notify_board(self) -> None:
        """Уведомить подписчиков о текущем состоянии платы."""
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
        """Уведомить подписчиков о текущем прогрессе теста."""
        self._emit(self._progress_callbacks, self.slot_id, self.step, self.test_time)

    def apply_report(self, report: dict) -> None:
        """Применить данные из отчёта к состоянию слота (только прогресс)."""
        result = report.get("result", {})
        if not isinstance(result, dict):
            return
        if "step" in result:
            self.step = int(result["step"])
            self.test_time = timedelta(seconds=self.step * 5)

    # ── Информация для UI ─────────────────────────────────
    def get_status_dict(
            self,
            ws_alive: bool,
            serial_alive: bool,
            log_watcher_alive: bool,
            is_logged_in: bool,
            last_log_lines: list[str],
    ) -> dict:
        """Получить словарь состояния для UI."""
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
            "last_log_lines": last_log_lines,
        }
