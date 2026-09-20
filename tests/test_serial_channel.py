import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.channels.base import ChannelDead
from app.channels.serial_channel import SerialChannel


@pytest.fixture
def mock_serial():
    """Создает фиктивный объект serial-порта."""
    ser = MagicMock()
    ser.is_open = True
    # in_waiting будет переопределен в тестах через PropertyMock
    ser.in_waiting = 0
    ser.write_async = AsyncMock()
    ser.read_async = AsyncMock()
    ser.close = MagicMock()
    return ser


@pytest.fixture
def patched_aioserial(mock_serial):
    """Патчит aioserial.AioSerial, чтобы он возвращал наш мок."""
    with patch("app.channels.serial_channel.aioserial.AioSerial", return_value=mock_serial) as mock_class:
        yield mock_class, mock_serial


async def test_start_and_stop_lifecycle(patched_aioserial):
    mock_class, mock_ser = patched_aioserial
    ch = SerialChannel(pc_id="slot1", port="COM3")

    await ch.start()
    assert ch.is_alive is True
    mock_class.assert_called_once_with(port="COM3", baudrate=115200, timeout=0.1)

    await ch.stop()
    assert ch.is_alive is False
    mock_ser.close.assert_called_once()


async def test_send_puts_bytes_on_wire(patched_aioserial):
    _, mock_ser = patched_aioserial
    ch = SerialChannel(pc_id="slot1", port="COM3")
    await ch.start()

    await ch.send({"type": "task", "task_id": "t1"})

    mock_ser.write_async.assert_called_once()
    called_args = mock_ser.write_async.call_args[0][0]
    assert b'"task_id":"t1"' in called_args
    assert called_args.endswith(b"\n")

    await ch.stop()


async def test_send_after_stop_raises(patched_aioserial):
    _, _ = patched_aioserial
    ch = SerialChannel(pc_id="slot1", port="COM3")
    await ch.start()
    await ch.stop()

    with pytest.raises(ChannelDead):
        await ch.send({"type": "task"})


async def test_incoming_ends_on_stop(patched_aioserial):
    _, _ = patched_aioserial
    ch = SerialChannel(pc_id="slot1", port="COM3")
    await ch.start()

    async def stopper():
        await asyncio.sleep(0.1)
        await ch.stop()

    asyncio.create_task(stopper())

    received = []
    async for msg in ch.incoming():
        received.append(msg)

    assert received == []


async def test_partial_message_reassembled_with_mock(patched_aioserial):
    """Эмуляция прихода сообщения частями (сборка буфера)."""
    _, mock_ser = patched_aioserial
    ch = SerialChannel(pc_id="slot1", port="COM3", idle_timeout=0.2)

    # Данные, которые будут приходить частями
    data_chunks = [
        b'{"type":"report","ta',
        b'sk_id":"t1"}\n',
    ]

    async def fake_read_async(n):
        if data_chunks:
            return data_chunks.pop(0)
        await asyncio.sleep(0.05)
        return b""

    mock_ser.read_async.side_effect = fake_read_async

    # in_waiting должен возвращать длину следующего чанка, если он есть, иначе 0
    # Используем patch.object для корректного мока property на уровне класса
    with patch.object(type(mock_ser), 'in_waiting', new_callable=PropertyMock) as mock_in_waiting:
        mock_in_waiting.side_effect = lambda: len(data_chunks[0]) if data_chunks else 0

        await ch.start()

        received = []

        # Оборачиваем цикл в отдельную корутину, чтобы wait_for мог её ограничить по времени
        async def reader():
            async for msg in ch.incoming():
                received.append(msg)
                break

        try:
            await asyncio.wait_for(reader(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        await ch.stop()

    assert len(received) == 1
    assert received[0]["type"] == "report"
    assert received[0]["task_id"] == "t1"