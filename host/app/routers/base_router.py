from abc import ABC
from typing import Dict

from host.app.channels.base import Channel


class BaseRouter(ABC):
    """Базовый класс сервера (роутера)"""
    def __init__(
            self,
            host: str = "0.0.0.0",
            port: int = 8765,
            hello_timeout: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.hello_timeout = hello_timeout

        self.is_started = False

    def register(self, channel: Channel) -> None:
        """Регистрация канала в списке допустимых каналов для сервера"""
        pass

    def start(self):
        """Запуск сервера"""
        pass

    def stop(self):
        """Остановка сервера"""
        pass

