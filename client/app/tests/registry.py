"""
Реестр тестов.
Регистрирует доступные тесты и предоставляет их по имени.
"""
import logging
from typing import Dict, Type
from .base import BaseTest

logger = logging.getLogger(__name__)


class TestRegistry:
    """Реестр доступных тестов."""

    def __init__(self):
        self._tests: Dict[str, Type[BaseTest]] = {}

    def register(self, test_class: Type[BaseTest]) -> None:
        """Зарегистрировать тест."""
        if not test_class.name:
            raise ValueError(f"Test class {test_class.__name__} has no name")
        self._tests[test_class.name] = test_class
        logger.info(f"Test registered: {test_class.name}")

    def get(self, name: str) -> BaseTest:
        """Получить экземпляр теста по имени."""
        test_class = self._tests.get(name)
        if test_class is None:
            raise ValueError(f"Unknown test: {name}")
        return test_class()

    def list_tests(self) -> list[str]:
        """Список зарегистрированных тестов."""
        return list(self._tests.keys())