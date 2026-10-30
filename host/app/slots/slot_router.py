"""
TaskRouter — маршрутизация задач между каналами слота.
Отвечает за:
- отправку задач по каналам с фейловером (route)
- ожидание отчётов с продлением по in_progress (_wait_report)
- чтение входящих сообщений и распределение их по ожидающим задачам (pump)
НЕ отвечает за:
- мониторинг здоровья каналов (это SlotMonitor)
- состояние слота (это SlotStateController)
"""
import asyncio
import logging
from typing import Callable

from host.app.channels.base import Channel, ChannelDead
from host.app.slots.exceptions import AllChannelsFailed, PendingTask
from host.app.slots.state import SlotState


class TaskRouter:
    """Маршрутизация задач между каналами с фейловером."""

    def __init__(
        self,
        slot_id: str,
        channels: list[Channel],
        set_offline: Callable[[str], None],
    ):
        self.slot_id = slot_id
        self.channels = channels
        self._set_offline = set_offline
        self.logger = logging.getLogger(f"slot_{slot_id}")

    async def route(self, payload: dict, record: PendingTask) -> dict:
        """Маршрутизация задачи с фейловером между каналами."""
        tried: list[str] = []
        last_err: Exception | None = None

        for ch in self.channels:
            if not ch.is_alive:
                continue
            tried.append(ch.name)

            try:
                await ch.send(payload)
                self.logger.info(f"task {record.task_id} -> {ch.name}")
            except ChannelDead as e:
                last_err = e
                self.logger.warning(f"send {ch.name} fail: {e}")
                continue

            try:
                report = await self._wait_report(record, ch)
                self.logger.info(
                    f"task {record.task_id} <- {ch.name} ({report.get('status')})"
                )
                return report
            except asyncio.TimeoutError as e:
                last_err = e
                self.logger.warning(
                    f"The channel:{ch.name} has exceeded the timeout limit: "
                    f"{record.task_id}. Trying another one"
                )
                continue
            except ChannelDead as e:
                last_err = e
                self.logger.warning(
                    f"The channel: {ch.name} died while the task: "
                    f"{record.task_id} was being executed. Trying another one"
                )
                continue

        # Все каналы исчерпаны
        if not any(ch.is_alive for ch in self.channels):
            self._set_offline("All channels failed")
        raise AllChannelsFailed(self.slot_id, record.task_id, tried, last_err)

    async def _wait_report(self, record: PendingTask, ch: Channel) -> dict:
        """Ждём финальный отчёт. Каждый in_progress продлевает окно."""
        while True:
            record.progress.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.final),
                    timeout=record.progress_timeout,
                )
            except asyncio.TimeoutError:
                if not ch.is_alive:
                    raise ChannelDead(
                        f"{ch.name} died while waiting {record.task_id}"
                    )
                if not record.progress.is_set():
                    raise
                continue
            return record.final.result()

    async def pump(self, ch: Channel, pending: dict[str, PendingTask]) -> None:
        """Насос: чтение отчётов из канала и распределение по ожидающим задачам."""
        try:
            async for msg in ch.incoming():
                mtype = msg.get("type")
                if mtype not in ("report", "error"):
                    continue
                tid = msg.get("task_id")
                if not tid:
                    continue
                record = pending.get(tid)
                if record is None:
                    continue
                record.on_report(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"pump {ch.name} is broken: {e}")