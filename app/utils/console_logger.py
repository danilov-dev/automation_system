from enum import Enum

from datetime import datetime


class LogLevel(Enum):
    INFO = "INFO"
    ERROR = "ERROR"
    WARNING = "WARNING"
    SUCCESS = "SUCCESS"
    DEBUG = "DEBUG"


class ConsoleLogger:
    # Словарь ANSI-кодов цветов
    _COLORS = {
        LogLevel.INFO: "\033[0;32m",  # Зеленый
        LogLevel.ERROR: "\033[0;31m",  # Красный
        LogLevel.WARNING: "\033[0;33m",  # Желтый
        LogLevel.SUCCESS: "\033[0;36m",  # Голубой
        LogLevel.DEBUG: "\033[30;42m",  # Черный на зеленом

    }
    _RESET = "\033[0m"  # Код сброса цвета

    @staticmethod
    def write(message: str, message_type: LogLevel = LogLevel.INFO):
        """
        Выводит отформатированное цветное сообщение.
        """
        color = ConsoleLogger._COLORS.get(message_type, "")
        reset_color = ConsoleLogger._RESET
        now = datetime.now()
        formatted_time = now.strftime("%d-%m-%Y %H:%M:%S")

        print(f"\n[{formatted_time}] {color}[{message_type.value}]{reset_color} {message}{ConsoleLogger._RESET}")