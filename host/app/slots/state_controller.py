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
StateCallback = Callable[[str, str, str], Union[None, Awaitable[None]]]


# Матрица допустимых переходов.
# Если перехода нет в этом словаре — он будет отклонён.
# Это защищает от случайных прыжков между состояниями и гонок.
VALID_TRANSITIONS: dict[SlotState, set[SlotState]] = {
    SlotState.IDLE:       {SlotState.STARTING},
    SlotState.STARTING:   {SlotState.CONNECTING, SlotState.REBOOTING, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.CONNECTING: {SlotState.READY, SlotState.REBOOTING, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.READY:      {SlotState.TESTING, SlotState.REBOOTING, SlotState.SHUTDOWN, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.TESTING:    {SlotState.READY, SlotState.REBOOTING, SlotState.SHUTDOWN, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.REBOOTING:  {SlotState.CONNECTING, SlotState.READY, SlotState.ERROR, SlotState.OFFLINE, SlotState.IDLE},
    SlotState.SHUTDOWN:   {SlotState.OFFLINE, SlotState.ERROR, SlotState.IDLE},
    SlotState.ERROR:      {SlotState.IDLE, SlotState.OFFLINE, SlotState.STARTING, SlotState.REBOOTING},
    SlotState.OFFLINE:    {SlotState.STARTING, SlotState.IDLE, SlotState.ERROR},
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

        # ── Подписчики ────────────────────────────────────
        self._state_callbacks: list[StateCallback] = []
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

    # ── Подписка на смену состояния ───────────────────────
    def on_state_change(self, cb: StateCallback) -> None:
        """
        Зарегистрировать колбэк смены состояния слота.
        Callback получит (slot_id, old_state_name, new_state_name).
        """
        self._state_callbacks.append(cb)

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

        # Уведомляем подписчиков
        for cb in self._state_callbacks:
            try:
                res = cb(self.slot_id, old_state.name, new_state.name)
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass
            except Exception as e:
                self.logger.error(f"state cb error: {e}")

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
            self.logger.debug("reboot detected")

    def on_shutdown_detected(self) -> None:
        """Обнаружено выключение платы (по логам COM)."""
        if self._transition(SlotState.SHUTDOWN):
            if self.board:
                self.board.reset()
            self.logger.debug("shutdown detected")

    def on_power_on_detected(self) -> None:
        """Обнаружено включение платы (начало загрузки, по логам COM)."""
        if self._state == SlotState.REBOOTING:
            self._transition(SlotState.CONNECTING)
        if self.board:
            self.board.bios = True
            self.board.touch()
        self.logger.debug("Board power-on detected")

    def on_login_prompt_detected(self) -> None:
        """Обнаружено приглашение логина — ОС загрузилась."""
        if self.board:
            self.board.os = True
            self.board.touch()
        self.logger.debug("login prompt detected")

    def on_fatal_error(self, error: str) -> None:
        """Критическая ошибка, слот не может работать."""
        self._transition(SlotState.ERROR, error)

    def on_slot_stopped(self) -> None:
        """Слот полностью остановлен (после вызова Slot.stop())."""
        self._transition(SlotState.IDLE)
        if self.board:
            self.board.reset()

    # ── Метрики и отчёты ──────────────────────────────────
    def update_metrics(self, v: float, a: float, wt: float) -> None:
        """Обновить показания датчиков питания."""
        self.v = v
        self.a = a
        self.wt = wt

    def apply_report(self, report: dict) -> None:
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