"""
PowerControl — управление питанием слота.
Сейчас — заглушка (симулирует включение/выключение и метрики).
Позже — реальное управление через GPIO / Serial-реле / IP-PDU.

Контракт:
power_on()               — подать питание
power_off()              — снять питание
is_on                    — подано ли питание
v / a / wt               — напряжение, ток, мощность (свойства)
on_power_change(cb)      — подписаться на вкл/выкл
on_metrics_change(cb)    — подписаться на изменение метрик питания
set_load_factor(f)       — множитель нагрузки (для симуляции тестов)

Симуляция внешних событий:
simulate_power_loss()    — симулировать обрыв питания
simulate_power_restore() — симулировать восстановление питания
"""
import asyncio
import logging
import random
from typing import Callable, Optional, Awaitable, Union

# callback(v, a, wt)
MetricsCallback = Callable[[float, float, float], Union[None, Awaitable[None]]]
# callback(is_on)
PowerCallback = Callable[[bool], None]


class PowerControl:
    """Заглушка управления питанием с симуляцией метрик."""

    def __init__(self, slot_id: str):
        self.pc_id = slot_id
        self._is_on = False
        self.logger = logging.getLogger(f"slot_{slot_id}")

        # ── Подписчики ──────────────────────────────────
        self._on_change_callbacks: list[PowerCallback] = []
        self._metrics_callbacks: list[MetricsCallback] = []

        # ── Метрики питания ─────────────────────────────
        self._v: float = 0.0
        self._a: float = 0.0
        self._wt: float = 0.0

        # ── Симуляция ───────────────────────────────────
        self._simulation_task: Optional[asyncio.Task] = None
        self._load_factor: float = 1.0  # множитель нагрузки (для симуляции тестов)

    # ── Свойства ────────────────────────────────────────
    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def v(self) -> float:
        return self._v

    @property
    def a(self) -> float:
        return self._a

    @property
    def wt(self) -> float:
        return self._wt

    # ── Подписка ────────────────────────────────────────
    def on_power_change(self, callback: PowerCallback) -> None:
        """Подписаться на вкл/выкл питания."""
        self._on_change_callbacks.append(callback)

    def on_metrics_change(self, callback: MetricsCallback) -> None:
        """Подписаться на изменение метрик питания (v, a, wt)."""
        self._metrics_callbacks.append(callback)

    # ── Команды управления ──────────────────────────────
    async def power_on(self) -> None:
        """Подать питание. Запускает симуляцию метрик."""
        if self._is_on:
            return
        await asyncio.sleep(0.1)  # имитация задержки реле
        self._is_on = True
        self.logger.info("POWER ON (stub)")
        self._notify_change(True)
        self._start_simulation()

    async def power_off(self) -> None:
        """Снять питание. Останавливает симуляцию и сбрасывает метрики."""
        if not self._is_on:
            return
        self._stop_simulation()
        self._is_on = False
        await asyncio.sleep(0.1)
        self.logger.info("POWER OFF (stub)")
        self._update_metrics(0.0, 0.0, 0.0)
        self._notify_change(False)

    async def current(self) -> float:
        """Ток потребления (для обратной совместимости)."""
        return self._a

    # ── Управление нагрузкой (для симуляции тестов) ─────
    def set_load_factor(self, factor: float) -> None:
        """Установить множитель нагрузки. 1.0 = простой, 2-3 = стресс-тест."""
        self._load_factor = max(0.0, factor)

    # ── Симуляция внешних событий ───────────────────────
    def simulate_power_loss(self) -> None:
        """Симулировать обрыв питания."""
        if self._is_on:
            self._stop_simulation()
            self._is_on = False
            self._update_metrics(0.0, 0.0, 0.0)
            self.logger.warning("POWER LOSS SIMULATED (stub)")
            self._notify_change(False)

    def simulate_power_restore(self) -> None:
        """Симулировать восстановление питания."""
        if not self._is_on:
            self._is_on = True
            self.logger.info("POWER RESTORE SIMULATED (stub)")
            self._notify_change(True)
            self._start_simulation()

    # ── Симуляция метрик ────────────────────────────────
    def _start_simulation(self) -> None:
        if self._simulation_task is None or self._simulation_task.done():
            self._simulation_task = asyncio.create_task(
                self._simulation_loop(),
                name=f"power-sim-{self.pc_id}",
            )

    def _stop_simulation(self) -> None:
        if self._simulation_task is not None:
            self._simulation_task.cancel()
            self._simulation_task = None

    async def _simulation_loop(self) -> None:
        """Фоновая симуляция метрик питания (заглушка реального реле)."""
        base_v = 12.0   # типичное питание платы
        base_a = 0.8    # ток в простое
        while True:
            try:
                # Плавные колебания с шумом
                v = base_v + random.uniform(-0.15, 0.15)
                a = base_a * self._load_factor + random.uniform(-0.05, 0.2)
                wt = v * a
                self._update_metrics(v, a, wt)
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"power simulation error: {e}")
                await asyncio.sleep(1.0)

    # ── Уведомления ─────────────────────────────────────
    def _update_metrics(self, v: float, a: float, wt: float) -> None:
        if (self._v, self._a, self._wt) == (v, a, wt):
            return
        self._v, self._a, self._wt = v, a, wt
        for cb in self._metrics_callbacks:
            try:
                res = cb(v, a, wt)
                if asyncio.iscoroutine(res):
                    try:
                        asyncio.get_running_loop().create_task(res)
                    except RuntimeError:
                        pass
            except Exception as e:
                self.logger.error(f"metrics callback error: {e}")

    def _notify_change(self, is_on: bool) -> None:
        for cb in self._on_change_callbacks:
            try:
                cb(is_on)
            except Exception as e:
                self.logger.error(f"power change callback error: {e}")