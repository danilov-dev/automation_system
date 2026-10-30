"""
Состояния слота (конечный автомат).
Вынесено в отдельный модуль, чтобы избежать циклических импортов
и позволить использовать SlotState в slot_monitor.py, slot_router.py
и других модулях без зависимости от slot.py.
"""
from enum import Enum, auto


class SlotState(Enum):
    IDLE = auto()          # Инициализирован, но не запущен
    STARTING = auto()      # Запускается
    CONNECTING = auto()    # Ждёт подключения клиента (WS handshake)
    READY = auto()         # Готов к работе
    TESTING = auto()       # Выполняет тест
    REBOOTING = auto()     # Перезагружается
    SHUTDOWN = auto()      # Выключается
    ERROR = auto()         # Ошибка
    OFFLINE = auto()       # Каналы недоступны