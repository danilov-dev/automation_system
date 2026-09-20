import asyncio

import pytest

from app.channels.base import ChannelDead
from app.pc_session import PcSession, AllChannelsFailed


class FakeChannel:
    """Тестовый Channel. Управляем вручную: alive/sent/incoming."""

    def __init__(self, name: str, alive: bool = True):
        self.name = name
        self._alive = alive
        self._incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.started = False
        self.stopped = False

    @property
    def is_alive(self) -> bool:
        return self._alive and not self.stopped

    def kill(self) -> None:
        self._alive = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        await self._incoming.put(None)

    async def send(self, msg: dict) -> None:
        if not self.is_alive:
            raise ChannelDead(f"[{self.name}] dead")
        self.sent.append(msg)

    async def incoming(self):
        while True:
            msg = await self._incoming.get()
            if msg is None:
                return
            yield msg

    async def push_report(self, msg: dict) -> None:
        await self._incoming.put(msg)


@pytest.fixture
async def session():
    primary = FakeChannel("tcp")
    backup = FakeChannel("serial")
    s = PcSession("slot1", primary, backup)
    await s.start()
    yield s, primary, backup
    await s.stop()

async def test_report_from_primary(session):
    s, primary, backup = session

    async def respond():
        while not primary.sent:
            await asyncio.sleep(0.01)
        tid = primary.sent[0]["task_id"]
        await primary.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "command": "ping", "result": {"msg": "pong"}}
        )

    asyncio.create_task(respond())
    report = await s.execute("ping", {"target": "8.8.8.8"},
                             progress_timeout=0.5)
    assert report["status"] == "ok"
    assert report["result"] == {"msg": "pong"}
    assert primary.sent and not backup.sent


async def test_primary_dead_uses_backup(session):
    s, primary, backup = session
    primary.kill()

    async def respond():
        while not backup.sent:
            await asyncio.sleep(0.01)
        tid = backup.sent[0]["task_id"]
        await backup.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "result": {"via": "backup"}}
        )

    asyncio.create_task(respond())
    report = await s.execute("ping", progress_timeout=0.3)
    assert report["result"]["via"] == "backup"
    assert not primary.sent and backup.sent


async def test_primary_dies_mid_wait_backup_used(session):
    """Ключевой сценарий: send в primary прошёл, потом primary умер."""
    s, primary, backup = session

    async def flow():
        # дождаться, пока task уйдёт в primary
        while not primary.sent:
            await asyncio.sleep(0.01)
        # primary умирает, пока мы ждём отчёт
        primary.kill()
        # ждём, пока execute переключится на backup
        for _ in range(100):
            if backup.sent:
                break
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("execute не переключился на backup")
        tid = backup.sent[0]["task_id"]
        await backup.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "result": {"via": "backup"}}
        )

    asyncio.create_task(flow())
    report = await s.execute("ping", progress_timeout=0.15)
    assert report["result"]["via"] == "backup"
    # task_id тот же — это важно для идемпотентности на PC
    assert primary.sent[0]["task_id"] == backup.sent[0]["task_id"]


async def test_primary_timeout_no_progress_backup_used(session):
    """Primary жив, но не отвечает. По тайм-ауту идём в backup."""
    s, primary, backup = session

    async def respond_backup():
        while not backup.sent:
            await asyncio.sleep(0.01)
        tid = backup.sent[0]["task_id"]
        await backup.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "result": {"via": "backup"}}
        )

    asyncio.create_task(respond_backup())
    report = await s.execute("ping", progress_timeout=0.15)
    assert report["result"]["via"] == "backup"


async def test_in_progress_extends_timeout(session):
    """
    Primary отвечает in_progress несколько раз, финал приходит позже,
    чем базовый progress_timeout. Но за счёт продлений — успеваем.
    """
    s, primary, backup = session

    async def flow():
        while not primary.sent:
            await asyncio.sleep(0.01)
        tid = primary.sent[0]["task_id"]
        for _ in range(3):
            await asyncio.sleep(0.1)               # > progress_timeout
            await primary.push_report(
                {"type": "report", "task_id": tid, "status": "in_progress"}
            )
        await asyncio.sleep(0.1)
        await primary.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "result": {"msg": "done"}}
        )

    asyncio.create_task(flow())
    report = await s.execute("slow", progress_timeout=0.15)
    assert report["status"] == "ok"
    assert not backup.sent


async def test_both_channels_dead_raises(session):
    s, primary, backup = session
    primary.kill()
    backup.kill()
    with pytest.raises(AllChannelsFailed):
        await s.execute("ping", progress_timeout=0.1)


async def test_duplicate_report_ignored(session):
    """Второй report с тем же task_id не должен ничего сломать."""
    s, primary, backup = session

    async def flow():
        while not primary.sent:
            await asyncio.sleep(0.01)
        tid = primary.sent[0]["task_id"]
        await primary.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "result": {"n": 1}}
        )
        await asyncio.sleep(0.05)
        # дубль — должен быть проигнорирован, не упасть с InvalidStateError
        await primary.push_report(
            {"type": "report", "task_id": tid, "status": "ok",
             "result": {"n": 2}}
        )

    asyncio.create_task(flow())
    report = await s.execute("ping", progress_timeout=0.3)
    assert report["result"]["n"] == 1
    # дать пампу обработать дубль
    await asyncio.sleep(0.1)
    assert True  # главное — не упало


async def test_two_parallel_tasks_do_not_block_each_other(session):
    """
    Быстрая задача на primary не должна ждать медленную.
    Обе идут параллельно, отчёты приходят в разном порядке.
    """
    s, primary, backup = session

    async def responder():
        seen: dict[str, str] = {}
        while len(seen) < 2:
            if primary.sent:
                for m in primary.sent:
                    tid = m["task_id"]
                    if tid in seen:
                        continue
                    seen[tid] = m["command"]
                    if m["command"] == "fast":
                        await asyncio.sleep(0.05)
                    else:
                        await asyncio.sleep(0.3)
                    await primary.push_report(
                        {"type": "report", "task_id": tid, "status": "ok",
                         "result": {"cmd": m["command"]}}
                    )
            await asyncio.sleep(0.01)

    asyncio.create_task(responder())

    t_fast = asyncio.create_task(s.execute("fast", progress_timeout=1.0))
    t_slow = asyncio.create_task(s.execute("slow", progress_timeout=1.0))

    fast = await asyncio.wait_for(t_fast, timeout=1.0)
    slow = await asyncio.wait_for(t_slow, timeout=2.0)
    assert fast["result"]["cmd"] == "fast"
    assert slow["result"]["cmd"] == "slow"


async def test_stop_unblocks_pending_execute(session):
    """Если session.stop() во время execute — execute должен упасть, а не висеть."""
    s, primary, backup = session

    async def flow():
        while not primary.sent:
            await asyncio.sleep(0.01)
        await s.stop()

    asyncio.create_task(flow())
    with pytest.raises(AllChannelsFailed):
        await asyncio.wait_for(s.execute("ping", progress_timeout=2.0),
                               timeout=3.0)


async def test_error_message_closes_waiting(session):
    """Сообщение type=error тоже завершает ожидание."""
    s, primary, backup = session

    async def flow():
        while not primary.sent:
            await asyncio.sleep(0.01)
        tid = primary.sent[0]["task_id"]
        await primary.push_report(
            {"type": "error", "task_id": tid, "message": "unknown command"}
        )

    asyncio.create_task(flow())
    report = await s.execute("bogus", progress_timeout=0.3)
    assert report["type"] == "error"
    assert report["message"] == "unknown command"