"""Система тестов."""
from .base import BaseTest
from .registry import TestRegistry
from .ping import PingTest
from .usb_benchmark import UsbBenchmarkTest
from .lan_benchmark import LanBenchmarkTest
from .serial_benchmark import SerialBenchmarkTest
from .dp_test import DpTest
from .stress_test import StressTest


def create_default_registry() -> TestRegistry:
    """Создать реестр с зарегистрированными тестами."""
    registry = TestRegistry()
    registry.register(PingTest)
    registry.register(UsbBenchmarkTest)
    registry.register(LanBenchmarkTest)
    registry.register(SerialBenchmarkTest)
    registry.register(DpTest)
    registry.register(StressTest)
    return registry


__all__ = [
    "BaseTest",
    "TestRegistry",
    "create_default_registry",
]