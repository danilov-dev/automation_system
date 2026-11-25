"""
Загрузка и валидация slots.yaml.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


class ConfigError(Exception):
    pass


@dataclass
class TcpConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    hello_timeout: float = 5.0


@dataclass
class WsConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    hello_timeout: float = 5.0
    ping_interval: float = 10.0
    ping_timeout: float = 10.0


@dataclass
class SlotConfig:
    slot_id: str
    com_log: str
    com_ctrl: str
    com_power: str
    login: str = "ad"
    password: str = "12345678"
    tcp_ip: Optional[str] = None


@dataclass
class AppConfig:
    ws: WsConfig
    slots: dict[str, SlotConfig] = field(default_factory=dict)

    def get_slot(self, slot_id: str) -> SlotConfig:
        if slot_id not in self.slots:
            raise ConfigError(f"slot {slot_id!r} не найден в конфиге")
        return self.slots[slot_id]


def load_config(path: str | Path) -> AppConfig:
    """Инициализация настроек. Загрузка конфигурационного файла"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"файл конфига не найден: {p}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"невалидный YAML: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("корень конфига должен быть словарём")

    ws_raw = raw.get("ws") or {}

    if not isinstance(ws_raw, dict):
        raise ConfigError("секция ws должна быть словарём")

    ws = WsConfig(
        host=str(ws_raw.get("host", "0.0.0.0")),
        port=int(ws_raw.get("port", 8765)),
        hello_timeout=float(ws_raw.get("hello_timeout", 5.0)),
        ping_interval=float(ws_raw.get("ping_interval", 10.0)),
        ping_timeout=float(ws_raw.get("ping_timeout", 10.0)),
    )

    slots_raw = raw.get("slots")
    if not isinstance(slots_raw, dict) or not slots_raw:
        raise ConfigError("секция slots пуста или отсутствует")

    slots: dict[str, SlotConfig] = {}
    for slot_id, s in slots_raw.items():
        if not isinstance(s, dict):
            raise ConfigError(f"slot {slot_id!r}: должен быть словарём")
        for req in ("com_log", "com_ctrl"):
            if not s.get(req):
                raise ConfigError(f"slot {slot_id!r}: не задан {req}")
        try:
            cfg = SlotConfig(
                slot_id=str(slot_id),
                com_log=str(s["com_log"]),
                com_ctrl=str(s["com_ctrl"]),
                com_power=str(s["com_power"]),
                login=str(s.get("login", "ad")),
                password=str(s.get("password", "12345678")),
                tcp_ip=(str(s["tcp_ip"]) if s.get("tcp_ip") else None),
            )
        except TypeError as e:
            raise ConfigError(f"slot {slot_id!r}: неверные поля ({e})") from e
        slots[cfg.slot_id] = cfg

    return AppConfig(ws=ws, slots=slots)
