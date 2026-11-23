import textwrap
from pathlib import Path

import pytest

from app.config import ConfigError, load_config


def write_tmp(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "slots.yaml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_loads_full_config(tmp_path):
    path = write_tmp(tmp_path, """
        ws:
          host: 127.0.0.1
          port: 9000
          hello_timeout: 3.0
          ping_interval: 4.0
          ping_timeout: 2.0
        slots:
          slot1:
            com_log: /dev/ttyUSB0
            com_ctrl: /dev/ttyUSB1
            login: root
            password: secret
    """)
    config = load_config(path)
    assert config.ws.host == "127.0.0.1"
    assert config.ws.port == 9000
    assert config.ws.hello_timeout == 3.0
    assert config.ws.ping_interval == 4.0
    assert config.ws.ping_timeout == 2.0
    assert set(config.slots) == {"slot1"}
    slot = config.slots["slot1"]
    assert slot.slot_id == "slot1"
    assert slot.com_log == "/dev/ttyUSB0"
    assert slot.login == "root"
    assert slot.password == "secret"


def test_defaults_applied(tmp_path):
    path = write_tmp(tmp_path, """
        slots:
          slot1:
            com_log: /dev/a
            com_ctrl: /dev/b
    """)
    config = load_config(path)
    assert config.ws.host == "0.0.0.0"
    assert config.ws.port == 8765
    assert config.slots["slot1"].login == "ad"


def test_missing_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/path.yaml")


def test_missing_required_field(tmp_path):
    path = write_tmp(tmp_path, """
        slots:
          slot1:
            com_log: /dev/a
    """)
    with pytest.raises(ConfigError, match="com_ctrl"):
        load_config(path)


def test_empty_slots(tmp_path):
    path = write_tmp(tmp_path, "slots: {}\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_slot_key_becomes_slot_id(tmp_path):
    path = write_tmp(tmp_path, """
        slots:
          myslot:
            com_log: /dev/a
            com_ctrl: /dev/b
    """)
    config = load_config(path)
    assert config.slots["myslot"].slot_id == "myslot"
