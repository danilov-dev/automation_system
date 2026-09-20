import asyncio

import pytest

from app.channels.base import ChannelDead
from app.channels.tcp import TcpChannel
from app.channels.tcp_router import TcpRouter
from app.protocol import encode


async def _connect_and_hello(port, pc_id):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(encode({"type": "hello", "pc_id": pc_id}))
    await writer.drain()
    await asyncio.sleep(0.05)
    return reader, writer


@pytest.fixture
async def setup():
    ch = TcpChannel("slot1")
    await ch.start()
    router = TcpRouter("127.0.0.1", port=0, hello_timeout=1.0)
    router.register(ch)
    await router.start()
    yield ch, router
    await router.stop()
    await ch.stop()


async def test_known_client_connects(setup):
    ch, router = setup
    reader, writer = await _connect_and_hello(router.actual_port, "slot1")
    assert ch.is_alive
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.1)
    assert not ch.is_alive


async def test_unknown_pc_id_rejected(setup):
    ch, router = setup
    reader, writer = await _connect_and_hello(router.actual_port, "slotX")
    # сервер должен закрыть соединение
    data = await asyncio.wait_for(reader.read(100), timeout=1.0)
    assert data == b""
    assert not ch.is_alive


async def test_no_hello_timeout(setup):
    ch, router = setup
    reader, writer = await asyncio.open_connection("127.0.0.1", router.actual_port)
    data = await asyncio.wait_for(reader.read(100), timeout=2.0)
    assert data == b""
    writer.close()
    await writer.wait_closed()


async def test_report_delivered_to_channel(setup):
    ch, router = setup
    reader, writer = await _connect_and_hello(router.actual_port, "slot1")

    writer.write(encode({"type": "report", "task_id": "t1", "status": "ok"}))
    await writer.drain()

    msg = await asyncio.wait_for(ch.incoming().__anext__(), timeout=1.0)
    assert msg == {"type": "report", "task_id": "t1", "status": "ok"}

    writer.close()
    await writer.wait_closed()


async def test_channel_send_reaches_client(setup):
    ch, router = setup
    reader, writer = await _connect_and_hello(router.actual_port, "slot1")

    await ch.send({"type": "task", "task_id": "t1", "command": "ping"})
    line = await asyncio.wait_for(reader.readline(), timeout=1.0)
    assert b'"task_id":"t1"' in line

    writer.close()
    await writer.wait_closed()


async def test_send_without_client_raises(setup):
    ch, _ = setup
    with pytest.raises(ChannelDead):
        await ch.send({"type": "task"})


async def test_reconnect_replaces_writer(setup):
    ch, router = setup
    r1, w1 = await _connect_and_hello(router.actual_port, "slot1")
    r2, w2 = await _connect_and_hello(router.actual_port, "slot1")
    await asyncio.sleep(0.1)

    assert ch.is_alive
    # первый writer должен быть закрыт сервером
    d = await asyncio.wait_for(r1.read(100), timeout=1.0)
    assert d == b""
    # через новый writer можно отправлять
    await ch.send({"type": "task", "task_id": "t2"})
    line = await asyncio.wait_for(r2.readline(), timeout=1.0)
    assert b'"task_id":"t2"' in line

    w2.close()
    await w2.wait_closed()


async def test_incoming_ends_on_stop(setup):
    ch, router = setup
    reader, writer = await _connect_and_hello(router.actual_port, "slot1")

    async def stopper():
        await asyncio.sleep(0.1)
        await ch.stop()

    asyncio.create_task(stopper())
    received = []
    async for msg in ch.incoming():
        received.append(msg)
    assert received == []
    writer.close()
    await writer.wait_closed()