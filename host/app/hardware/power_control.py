"""
PowerControl — управление питанием слота.
Сейчас — заглушка (симулирует включение/выключение).
Позже — реальное управление через GPIO / Serial-реле / IP-PDU.

Контракт:
power_on()               — подать питание (команда от хоста)
power_off()              — снять питание (команда от хоста)
is_on                    — свойство: подано ли питание
current()                — ток потребления (пока 0.0, потом INA219)

Для тестов и будущей интеграции с реальным реле/GPIO:
simulate_power_loss()    — симулировать обрыв питания (например, выбило пробки)
simulate_power_restore() — симулировать восстановление питания
on_power_change(cb)      — подписаться на изменение состояния питания
                         (в будущем реальное реле само будет дёргать этот callback)
"""
import asyncio
import logging
from typing import Callable, Optional

from host.app.slots.state import SlotState


class PowerControl:
    """Заглушка управления питанием с возможностью симуляции событий."""

    def __init__(self, slot_id: str):
        self.pc_id = slot_id
        self._is_on = False
        self._on_change_callbacks: list[Callable[[bool], None]] = []
        self.logger = logging.getLogger(f"slot_{slot_id}")

    # ── Свойства ────────────────────────────────────────
    @property
    def is_on(self) -> bool:
        return self._is_on

    # ── Команды управления (от хоста к железу) ──────────
    async def power_on(self) -> None:
        """Подать питание. В заглушке — просто меняем флаг."""
        if self._is_on:
            return
        await asyncio.sleep(0.1)  # имитация задержки включения реле
        self._is_on = True
        self.logger.info("POWER ON (stub)")
        self._notify_change(True)

    async def power_off(self) -> None:
        """Снять питание. В заглушке — просто меняем флаг."""
        if not self._is_on:
            return
        self._is_on = False
        await asyncio.sleep(0.1)  # имитация задержки выключения реле
        self.logger.info("POWER OFF (stub)")
        self._notify_change(False)

    async def current(self) -> float:
        """Ток потребления в амперах. Пока заглушка."""
        return 0.0 if not self._is_on else 1.5  # имитация

    # ── Симуляция внешних событий (для тестов и будущего реле) ──
    def simulate_power_loss(self) -> None:
        """
        Симулировать обрыв питания (например, выбило пробки, отвалилось реле).
        В будущем этот метод будет вызываться драйвером GPIO/реле при обнаружении
        реального обрыва питания на стороне платы.
        """
        if self._is_on:
            self._is_on = False
            self.logger.warning("POWER LOSS SIMULATED (stub)")
            self._notify_change(False)

    def simulate_power_restore(self) -> None:
        """
        Симулировать восстановление питания.
        В будущем — драйвер GPIO/реле сам вызовет это при появлении напряжения.
        """
        if not self._is_on:
            self._is_on = True
            self.logger.info("POWER RESTORE SIMULATED (stub)")
            self._notify_change(True)

    # ── Подписка на изменения (для будущего реле/GPIO) ──
    def on_power_change(self, callback: Callable[[bool], None]) -> None:
        """
        Подписаться на изменение состояния питания.
        Callback получит True при включении и False при выключении.

        В будущем, когда будет реальное реле/GPIO, оно будет само дёргать
        эти callback'и при обнаружении изменения напряжения, и SlotMonitor
        сможет реагировать мгновенно, без опроса.
        """
        self._on_change_callbacks.append(callback)

    def _notify_change(self, is_on: bool) -> None:
        """Уведомить подписчиков об изменении состояния питания."""
        for cb in self._on_change_callbacks:
            try:
                cb(is_on)
            except Exception as e:
                self.logger.error(f"power change callback error: {e}")
