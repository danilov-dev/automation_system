"""
Базовый класс для всех тестов.
Каждый тест:
- принимает параметры
- отправляет промежуточные отчёты (in_progress)
- возвращает финальный результат
"""
import asyncio
from typing import Callable, Awaitable

# Тип для колбэка отправки отчёта
ReportCallback = Callable[[dict], Awaitable[None]]


class BaseTest:
    """Базовый класс для теста."""

    name: str = "base"
    description: str = "Base test"

    async def run(self, params: dict, report_cb: ReportCallback) -> dict:
        """
        Выполнить тест.

        Args:
            params: параметры теста
            report_cb: колбэк для отправки промежуточных отчётов

        Returns:
            dict с результатом теста
        """
        raise NotImplementedError