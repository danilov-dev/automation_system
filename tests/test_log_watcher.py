import asyncio
import os
import pty
import select

import pytest

from app.log_watcher import LogWatcher
from app.boot_automation import BootAutomation


@pytest.fixture
def pty_pair():
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    os.set_blocking(master, False)
    yield master, slave_name
    os.close(master)
    os.close(slave)


def read_master(master: int, timeout: float = 1.0) -> bytes:
    r, _, _ = select.select([master], [], [], timeout)
    if not r:
        return b""
    data = b""
    while True:
        try:
            chunk = os.read(master, 4096)
        except BlockingIOError:
            break
        if not chunk:
            break
        data += chunk
    return data


async def test_line_dispatched_to_callback(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    got = asyncio.Event()
    seen: list[str] = []

    async def on_kernel(line: str):
        seen.append(line)
        got.set()

    w.on(["kernel:"], on_kernel)
    await w.start()
    try:
        os.write(master, b"kernel: booting\n")
        await asyncio.wait_for(got.wait(), timeout=1.0)
        assert seen == ["kernel: booting"]
    finally:
        await w.stop()


async def test_pattern_case_insensitive(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    got = asyncio.Event()

    w.on(["LOGIN:"], lambda _l: got.set())
    await w.start()
    try:
        os.write(master, b"login: \n")
        await asyncio.wait_for(got.wait(), timeout=1.0)
    finally:
        await w.stop()


async def test_multiple_rules_all_fire(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    hits = {"a": 0, "b": 0, "c": 0}

    w.on(["systemd"], lambda _l: hits.__setitem__("a", hits["a"] + 1))
    w.on(["starting"], lambda _l: hits.__setitem__("b", hits["b"] + 1))
    w.on(["nope"], lambda _l: hits.__setitem__("c", hits["c"] + 1))

    await w.start()
    try:
        os.write(master, b"systemd starting unit\n")
        await asyncio.sleep(0.2)
        assert hits == {"a": 1, "b": 1, "c": 0}
    finally:
        await w.stop()


async def test_ansi_stripped(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    got = asyncio.Event()
    seen: list[str] = []

    async def cb(line: str):
        seen.append(line)
        got.set()

    w.on(["reboot"], cb)
    await w.start()
    try:
        os.write(master, b"\x1b[1;31mreboot: now\x1b[0m\n")
        await asyncio.wait_for(got.wait(), timeout=1.0)
        assert seen == ["reboot: now"]
    finally:
        await w.stop()


async def test_idle_timeout_flushes_without_newline(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    got = asyncio.Event()
    seen: list[str] = []

    async def cb(line: str):
        seen.append(line)
        got.set()

    w.on(["partial"], cb)
    await w.start()
    try:
        os.write(master, b"partial line without newline")
        await asyncio.wait_for(got.wait(), timeout=1.0)
        assert seen == ["partial line without newline"]
    finally:
        await w.stop()


async def test_buffer_keeps_last_lines(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1, buffer_size=3)
    await w.start()
    try:
        for i in range(5):
            os.write(master, f"line-{i}\n".encode())
        await asyncio.sleep(0.3)
        assert list(w.lines) == ["line-2", "line-3", "line-4"]
    finally:
        await w.stop()


async def test_send_line_writes_to_port(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    await w.start()
    try:
        await w.send_line("mylogin")
        await asyncio.sleep(0.1)
        data = read_master(master)
        assert data == b"mylogin\n"
    finally:
        await w.stop()


async def test_send_line_after_stop_noop(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    await w.start()
    await w.stop()
    await w.send_line("should not appear")
    # Не падаем, ничего не пишем. Просто проверяем, что не исключение.


async def test_boot_automation_login_then_password(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    boot = BootAutomation(w, login="ad", password="12345678",
                          success_patterns=["~ #", "~ $"])
    await w.start()
    try:
        os.write(master, b"login:")
        await asyncio.sleep(0.2)
        assert read_master(master) == b"ad\n"

        os.write(master, b"Password:")
        await asyncio.sleep(0.2)
        assert read_master(master) == b"12345678\n"

        os.write(master, b"root@host:~# ")
        await asyncio.sleep(0.2)
        assert boot.is_logged_in
    finally:
        await w.stop()


async def test_boot_automation_does_not_resend_login(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    boot = BootAutomation(w, login="ad", password="pw")
    await w.start()
    try:
        os.write(master, b"login:")
        await asyncio.sleep(0.2)
        _ = read_master(master)          # съедаем первый логин
        os.write(master, b"login:")       # повторный запрос подряд
        await asyncio.sleep(0.2)
        # не должно быть второго "ad\n"
        assert read_master(master) == b""
    finally:
        await w.stop()


async def test_boot_automation_reset_on_new_login(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    boot = BootAutomation(w, login="ad", password="pw",
                          success_patterns=["~#"])
    await w.start()
    try:
        os.write(master, b"login:")
        await asyncio.sleep(0.15)
        os.write(master, b"Password:")
        await asyncio.sleep(0.15)
        os.write(master, b"root@x:~# ")
        await asyncio.sleep(0.15)
        assert boot.is_logged_in

        # PC перезагрузился и снова просит логин
        os.write(master, b"login:")
        await asyncio.sleep(0.2)
        assert not boot.is_logged_in
        # и логин снова отправляется
        assert read_master(master) == b"ad\n"
    finally:
        await w.stop()


async def test_stop_cancels_pending_callbacks(pty_pair):
    master, slave_name = pty_pair
    w = LogWatcher("slot1", slave_name, idle_timeout=0.1)
    started = asyncio.Event()

    async def slow(_line: str):
        started.set()
        await asyncio.sleep(10)

    w.on(["slow"], slow)
    await w.start()
    try:
        os.write(master, b"slow\n")
        await asyncio.wait_for(started.wait(), timeout=1.0)
    finally:
        await w.stop()
    # если stop() не отменил задачу — тест бы завис на 10 сек


async def test_open_nonexistent_raises():
    w = LogWatcher("slot1", "/dev/ttyDOESNOTEXIST")
    with pytest.raises(RuntimeError):
        await w.start()