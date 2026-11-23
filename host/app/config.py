"""Load and validate slots.yaml."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Invalid application configuration."""


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
    login: str = "ad"
    password: str = "12345678"


@dataclass
class AppConfig:
    ws: WsConfig
    slots: dict[str, SlotConfig] = field(default_factory=dict)

    def get_slot(self, slot_id: str) -> SlotConfig:
        try:
            return self.slots[slot_id]
        except KeyError as exc:
            raise ConfigError(f"slot {slot_id!r} not found in config") from exc


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    ws_raw = raw.get("ws") or {}
    if not isinstance(ws_raw, dict):
        raise ConfigError("ws section must be a mapping")

    try:
        ws = WsConfig(
            host=str(ws_raw.get("host", "0.0.0.0")),
            port=int(ws_raw.get("port", 8765)),
            hello_timeout=float(ws_raw.get("hello_timeout", 5.0)),
            ping_interval=float(ws_raw.get("ping_interval", 10.0)),
            ping_timeout=float(ws_raw.get("ping_timeout", 10.0)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid ws configuration: {exc}") from exc

    if not 0 <= ws.port <= 65535:
        raise ConfigError("ws.port must be between 0 and 65535")
    if ws.hello_timeout <= 0 or ws.ping_interval <= 0 or ws.ping_timeout <= 0:
        raise ConfigError("ws timeouts must be positive")

    slots_raw = raw.get("slots")
    if not isinstance(slots_raw, dict) or not slots_raw:
        raise ConfigError("slots section is empty or missing")

    slots: dict[str, SlotConfig] = {}
    for raw_slot_id, slot_raw in slots_raw.items():
        slot_id = str(raw_slot_id)
        if not isinstance(slot_raw, dict):
            raise ConfigError(f"slot {slot_id!r} must be a mapping")
        for required in ("com_log", "com_ctrl"):
            if not slot_raw.get(required):
                raise ConfigError(f"slot {slot_id!r}: missing {required}")
        slots[slot_id] = SlotConfig(
            slot_id=slot_id,
            com_log=str(slot_raw["com_log"]),
            com_ctrl=str(slot_raw["com_ctrl"]),
            login=str(slot_raw.get("login", "ad")),
            password=str(slot_raw.get("password", "12345678")),
        )

    return AppConfig(ws=ws, slots=slots)
