import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.protocol import State

from app.channels.base import ChannelDead
from app.channels.ws_channel import WsChannel


def make_ws():
    ws = MagicMock()
    ws.state = State.OPEN
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


async def test_ws_channel_lifecycle_and_send():
    channel = WsChannel("slot1")
    ws = make_ws()
    await channel.start()
    await channel.attach_ws(ws)
    assert channel.is_alive
    await channel.send({"type": "task", "task_id": "t1"})
    ws.send.assert_awaited_once()
    await channel.stop()
    assert not channel.is_alive
    with pytest.raises(ChannelDead):
        await channel.send({"type": "task"})


async def test_incoming_queue_is_recreated_after_restart():
    channel = WsChannel("slot1")
    await channel.start()
    await channel.push({"type": "report", "task_id": "old"})
    await channel.stop()

    await channel.start()
    await channel.push({"type": "report", "task_id": "new"})
    iterator = channel.incoming()
    assert await iterator.__anext__() == {"type": "report", "task_id": "new"}
    await channel.stop()


async def test_old_connection_cannot_detach_new_connection():
    channel = WsChannel("slot1")
    old_ws = make_ws()
    new_ws = make_ws()
    await channel.start()
    await channel.attach_ws(old_ws)
    await channel.attach_ws(new_ws)
    channel.detach(old_ws)
    assert channel.ws is new_ws
    await channel.stop()
