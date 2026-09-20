import textwrap
from pathlib import Path

import pytest

from app.config import load_config
from app.registry import Registry
from app.channels.tcp import TcpChannel
from app.channels.serial_channel import SerialChannel


def make_cfg(tmp_path, n_slots: int = 2):
    slots_yaml = "\n".join(
        f"""
  slot{i}:
    com_log: /dev/null
    com_ctrl: /dev/null
"""
        for i in range(1, n_slots + 1)
    )
    content = "slots:\n" + slots_yaml
    p = tmp_path / "slots.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return load_config(p)


def test_registry_builds_all_slots(tmp_path):
    cfg = make_cfg(tmp_path, n_slots=4)
    reg = Registry(cfg)
    assert set(reg.all_pc_ids()) == {"slot1", "slot2", "slot3", "slot4"}
    for pc_id in reg.all_pc_ids():
        b = reg.get(pc_id)
        assert b is not None
        assert isinstance(b.tcp_channel, TcpChannel)
        assert isinstance(b.serial_channel, SerialChannel)
        assert b.pc_session.pc_id == pc_id


def test_router_knows_all_channels(tmp_path):
    cfg = make_cfg(tmp_path, n_slots=3)
    reg = Registry(cfg)
    # внутри router._channels — приватное, но для теста ок
    assert set(reg.router._channels.keys()) == {"slot1", "slot2", "slot3"}


def test_get_unknown_returns_none(tmp_path):
    cfg = make_cfg(tmp_path, n_slots=1)
    reg = Registry(cfg)
    assert reg.get("slotX") is None