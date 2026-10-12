"""
Единый протокол обмена для TCP и Serial.

Формат: JSON-объект + '\n' (line-delimited JSON).
Один и тот же на обоих транспортах — PC-сервис не знает,
через какой канал пришло сообщение.

Типы сообщений (поле "type"):
  PC -> RPi:
    {"type": "hello",  "pc_id": "slot1"}                  # при TCP-подключении
    {"type": "report", "task_id": "...", "status": "ok",
                       "command": "...", "result": {...}}  # финальный отчёт
    {"type": "report", "task_id": "...", "status": "in_progress"}
    {"type": "error",  "task_id": "...", "message": "..."}

  RPi -> PC:
    {"type": "task",   "task_id": "...",
                       "command": "ping", "params": {...}}
"""
import json
from typing import Optional


PROTOCOL_VERSION = 1


def encode(msg: dict) -> bytes:
    """dict -> bytes с завершающим \\n."""
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> Optional[dict]:
    """
    bytes -> dict или None.
    Используем backslashreplace, чтобы любой бинарный мусор
    превращался в читаемый вид (например, \xff), а не в .
    """
    s = line.decode("utf-8", errors="backslashreplace").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return {"type": "raw", "text": s}
    return obj if isinstance(obj, dict) else {"type": "raw", "text": s}