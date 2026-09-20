"""
Интеграционный тест: собираем SessionManager на pty-парах,
подключаемся TCP-клиентом, гоняем execute, шлём reboot в COM1.
"""
import asyncio
import os
import pty
import textwrap
from pathlib import Path

import pytest

from app.config import load_config
from app.registry import Registry
from app.session_manager import SessionManager
from app.protocol import encode, decode


@pytest.fixture
def pty_pair():
    master, slave = pty.openpty()
    name = os.ttyname(slave)
    os.set_blocking(master, False)
    yield master, name
    os.close(master)
    os.close(slave)


@pytest.fixture
def cfg_with_slot(tmp_path, pty_pair):
    com_log_m, com_log_p = pty_pair

    master2, slave2 = pty.openpty()
    com_ctrl_p = os.ttyname(slave2)
    os.set_blocking(master2, False)

    content = f"""
tcp:
  host: 127.0.0.1
  port: 0
  hello_timeout: 2.0
slots:
  slot1:
    com_log: {com_log_p}
    com_ctrl: {com_ctrl_p}
    login: ad
    password: pw
"""
    p = tmp_path / "slots.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    cfg = load_config(p)

    yield cfg, com_log_m, master2

    os.close(master2)
    os.close(slave2)


async def _tcp_hello(port, pc_id):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(encode({"type": "hello", "pc_id": pc_id}))
    await writer.drain()
    await asyncio.sleep(0.05)
    return reader, writer


async def test_full_stack_execute_over_tcp(cfg_with_slot):
    cfg, com_log_m, com_ctrl_m = cfg_with_slot
    reg = Registry(cfg)
    mgr = SessionManager(reg)
    await mgr.start_all()
    try:
        port = reg.router.actual_port
        reader, writer = await _tcp_hello(port, "slot1")

        async def responder():
            line = await reader.readline()
            msg = decode(line)
            assert msg["type"] == "task"
            rep = {
                "type": "report",
                "task_id": msg["task_id"],
                "status": "ok",
                "command": msg["command"],
                "result": {"msg": "pong"},
            }
            writer.write(encode(rep))
            await writer.drain()

        asyncio.create_task(responder())
        report = await mgr.execute("slot1", "ping", {"target": "8.8.8.8"})
        assert report["status"] == "ok"
        assert report["result"] == {"msg": "pong"}

        writer.close()
        await writer.wait_closed()
    finally:
        await mgr.stop_all()


async def test_fallback_to_serial_when_tcp_dead(cfg_with_slot):
    """TCP клиент не подключён — задача уходит в COM2, отвечает там."""
    cfg, com_log_m, com_ctrl_m = cfg_with_slot
    reg = Registry(cfg)
    mgr = SessionManager(reg)
    await mgr.start_all()
    try:
        async def serial_responder():
            # читаем, пока не увидим task
            buf = b""
            for _ in range(200):
                try:
                    chunk = os.read(com_ctrl_m, 4096)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = decode(line)
                        if msg and msg.get("type") == "task":
                            rep = {
                                "type": "report",
                                "task_id": msg["task_id"],
                                "status": "ok",
                                "result": {"via": "serial"},
                            }
                            os.write(com_ctrl_m, encode(rep))
                            return
                await asyncio.sleep(0.02)
            raise RuntimeError("task не пришёл по serial")

        asyncio.create_task(serial_responder())
        report = await mgr.execute("slot1", "ping")
        assert report["result"]["via"] == "serial"
    finally:
        await mgr.stop_all()


async def test_reboot_in_com_log_resets_boot_automation(cfg_with_slot):
    cfg, com_log_m, com_ctrl_m = cfg_with_slot
    reg = Registry(cfg)
    mgr = SessionManager(reg)

    states: list[tuple[str, str]] = []
    mgr.on_state_change(lambda pc_id, state: states.append((pc_id, state)))

    await mgr.start_all()
    try:
        bundle = reg.get("slot1")
        bundle.boot_automation.is_logged_in = True  # эмулируем «мы в шелле»

        os.write(com_log_m, b"systemd-reboot.service - system reboot\n")
        # дать LogWatcher-у обработать и колбэку отработать
        for _ in range(50):
            await asyncio.sleep(0.02)
            if states:
                break

        assert states == [("slot1", "reboot")]
        assert bundle.boot_automation.is_logged_in is False
    finally:
        await mgr.stop_all()


async def test_shutdown_in_com_log(cfg_with_slot):
    cfg, com_log_m, com_ctrl_m = cfg_with_slot
    reg = Registry(cfg)
    mgr = SessionManager(reg)

    states: list[tuple[str, str]] = []
    mgr.on_state_change(lambda pc_id, state: states.append((pc_id, state)))

    await mgr.start_all()
    try:
        os.write(com_log_m, b"systemd-shutdown[1]: Powering off\n")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if states:
                break
        assert states == [("slot1", "shutdown")]
    finally:
        await mgr.stop_all()


async def test_execute_unknown_slot_raises(cfg_with_slot):
    cfg, _, _ = cfg_with_slot
    reg = Registry(cfg)
    mgr = SessionManager(reg)
    with pytest.raises(KeyError):
        await mgr.execute("slotX", "ping")