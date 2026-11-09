from enum import Enum, auto
from typing import Callable, Any

class ClientEvent(Enum):
    """События клиента"""
    CONNECTED = auto()
    DISCONNECTED = auto()
    MESSAGE_RECEIVED = auto()

ClientEventHandler = Callable[[ClientEvent, Any], Any]