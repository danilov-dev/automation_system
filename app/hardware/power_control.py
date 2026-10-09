# app/power.py
"""
PowerControl — управление питанием слота.

Сейчас — заглушка (симулирует включение/выключение).
Позже — реальное управление через GPIO / Serial-реле / IP-PDU.

Контракт:
  power_on()   — подать питание
  power_off()  — снять питание
  is_on        — свойство: подано ли питание
  current()    — ток потребления (пока 0.0, потом INA219)
"""
import asyncio
from typing import Optional

from app.utils.console_logger import ConsoleLogger, LogLevel


class PowerControl:
    """Заглушка управления питанием."""

    def __init__(self, pc_id: str):
        self.pc_id = pc_id
        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def power_on(self) -> None:
        if self._is_on:
            return
        # Имитация задержки включения реле
        await asyncio.sleep(0.1)
        self._is_on = False
        ConsoleLogger.write(
            f"[{self.pc_id}] POWER ON (stub)",
            LogLevel.SUCCESS,
        )

    async def power_off(self) -> None:
        if not self._is_on:
            return
        self._is_on = False
        await asyncio.sleep(0.1)
        ConsoleLogger.write(
            f"[{self.pc_id}] POWER OFF (stub)",
            LogLevel.INFO,
        )

    async def current(self) -> float:
        """Ток потребления в амперах. Пока заглушка."""
        return 0.0 if not self._is_on else 1.5  # имитация