import asyncio
import json

import pytest
import websockets
from websockets.asyncio.client import connect

from app.channels.ws_channel import WsChannel
from app.routers.ws_router import WsRouter
from app.protocol import decode, encode


async def wait_until(predicate, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


async def test_ws_router_handshake_and_message_roundtrip():
    channel = WsChannel("slot1")
    router = WsRouter(host="127.0.0.1", port=0)
    router.register(channel)
    await channel.start()
    await router.start()
    try:
        async with connect(f"ws://127.0.0.1:{router.actual_port}") as ws:
            await ws.send(json.dumps({"type": "hello", "slot_id": "slot1"}))
            await wait_until(lambda: channel.is_alive)

            await channel.send({"type": "task", "task_id": "t1"})
            outbound = decode(await asyncio.wait_for(ws.recv(), timeout=1.0))
            assert outbound == {"type": "task", "task_id": "t1"}

            await ws.send(encode({"type": "report", "task_id": "t1"}).decode())
            incoming = await asyncio.wait_for(
                channel.incoming().__anext__(), timeout=1.0
            )
            assert incoming == {"type": "report", "task_id": "t1"}
    finally:
        await router.stop()
        await channel.stop()


async def test_ws_router_reconnect_replaces_old_connection():
    channel = WsChannel("slot1")
    router = WsRouter(host="127.0.0.1", port=0)
    router.register(channel)
    await channel.start()
    await router.start()
    uri = f"ws://127.0.0.1:{router.actual_port}"
    try:
        old_ws = await connect(uri)
        await old_ws.send(json.dumps({"type": "hello", "slot_id": "slot1"}))
        await wait_until(lambda: channel.is_alive)

        new_ws = await connect(uri)
        await new_ws.send(json.dumps({"type": "hello", "slot_id": "slot1"}))
        await new_ws.send(encode({"type": "report", "task_id": "ready"}).decode())
        ready = await asyncio.wait_for(channel.incoming().__anext__(), timeout=1.0)
        assert ready["task_id"] == "ready"

        await channel.send({"type": "task", "task_id": "new"})
        message = decode(await asyncio.wait_for(new_ws.recv(), timeout=1.0))
        assert message["task_id"] == "new"
        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(old_ws.recv(), timeout=1.0)
        await new_ws.close()
    finally:
        await router.stop()
        await channel.stop()


async def test_ws_router_rejects_unknown_slot():
    channel = WsChannel("slot1")
    router = WsRouter(host="127.0.0.1", port=0)
    router.register(channel)
    await router.start()
    try:
        async with connect(f"ws://127.0.0.1:{router.actual_port}") as ws:
            await ws.send(json.dumps({"type": "hello", "slot_id": "missing"}))
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), timeout=1.0)
    finally:
        await router.stop()
        await channel.stop()
