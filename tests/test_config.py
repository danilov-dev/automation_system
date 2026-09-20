import textwrap
from pathlib import Path

import pytest

from app.config import load_config, ConfigError


def write_tmp(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "slots.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_loads_full_config(tmp_path):
    p = write_tmp(tmp_path, """
        tcp:
          host: 127.0.0.1
          port: 9000
          hello_timeout: 3.0
        slots:
          slot1:
            com_log: /dev/ttyUSB0
            com_ctrl: /dev/ttyUSB1
            login: root
            password: secret
            tcp_ip: 10.0.0.1
    """)
    cfg = load_config(p)
    assert cfg.tcp.host == "127.0.0.1"
    assert cfg.tcp.port == 9000
    assert cfg.tcp.hello_timeout == 3.0
    assert set(cfg.slots) == {"slot1"}
    s = cfg.slots["slot1"]
    assert s.pc_id == "slot1"
    assert s.com_log == "/dev/ttyUSB0"
    assert s.login == "root"
    assert s.password == "secret"
    assert s.tcp_ip == "10.0.0.1"


def test_defaults_applied(tmp_path):
    p = write_tmp(tmp_path, """
        slots:
          slot1:
            com_log: /dev/a
            com_ctrl: /dev/b
    """)
    cfg = load_config(p)
    assert cfg.tcp.host == "0.0.0.0"
    assert cfg.tcp.port == 8765
    assert cfg.slots["slot1"].login == "ad"
    assert cfg.slots["slot1"].tcp_ip is None


def test_missing_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/path.yaml")


def test_missing_required_field(tmp_path):
    p = write_tmp(tmp_path, """
        slots:
          slot1:
            com_log: /dev/a
    """)
    with pytest.raises(ConfigError, match="com_ctrl"):
        load_config(p)


def test_empty_slots(tmp_path):
    p = write_tmp(tmp_path, "slots: {}\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_slot_key_becomes_pc_id(tmp_path):
    p = write_tmp(tmp_path, """
        slots:
          myslot:
            com_log: /dev/a
            com_ctrl: /dev/b
    """)
    cfg = load_config(p)
    assert "myslot" in cfg.slots
    assert cfg.slots["myslot"].pc_id == "myslot"