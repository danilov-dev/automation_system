"""
TaskRouter — маршрутизация задач между каналами слота.
Отвечает за:
— отправку задач по каналам с фейловером (route)
— ожидание отчётов с продлением по in_progress (_wait_report)
— чтение входящих сообщений и распределение их по ожидающим задачам (pump)
— управление насосами (start_pumps, stop_pumps)
НЕ отвечает за:
— мониторинг здоровья каналов (это SlotMonitor)
— состояние слота (это SlotStateController)
— владение pending-задачами (это SlotTestRunner)
"""
import asyncio
import logging
from contextlib import suppress
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
        pending_provider: Callable[[], dict[str, PendingTask]],
    ):
        self.slot_id = slot_id
        self.channels = channels
        self._set_offline = set_offline
        self._pending_provider = pending_provider
        self._pumps: list[asyncio.Task] = []
        self.logger = logging.getLogger(f"slot_{slot_id}")

    async def route(self, payload: dict, record: PendingTask) -> dict:
        """Маршрутизация задачи с фейловером между каналами."""
        tried: list[str] = []
        last_err: Exception | None = None

        for channel in self.channels:
            if not channel.is_alive:
                continue
            tried.append(channel.name)
            try:
                await channel.send(payload)
                self.logger.info(f"task {record.task_id} -> {channel.name}")
            except ChannelDead as e:
                last_err = e
                self.logger.warning(f"send {channel.name} fail: {e}")
                continue
            try:
                report = await self._wait_report(record, channel)
                self.logger.info(
                    f"task {record.task_id} <- {channel.name} ({report.get('status')})"
                )
                return report
            except asyncio.TimeoutError as e:
                last_err = e
                self.logger.warning(
                    f"The channel:{channel.name} has exceeded the timeout limit: "
                    f"{record.task_id}. Trying another one"
                )
                continue
            except ChannelDead as e:
                last_err = e
                self.logger.warning(
                    f"The channel: {channel.name} died while the task: "
                    f"{record.task_id} was being executed. Trying another one"
                )
                continue

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

    def start_pumps(self) -> None:
        """Запустить насосы для всех каналов."""
        self.stop_pumps_request()
        pending = self._pending_provider()
        self._pumps = [
            asyncio.create_task(
                self.pump(ch, pending),
                name=f"pump-{self.slot_id}-{ch.name}",
            )
            for ch in self.channels
        ]
        self.logger.info(f"Started {len(self._pumps)} pumps")

    async def stop_pumps(self) -> None:
        """Остановить все насосы."""
        for t in self._pumps:
            t.cancel()
        for t in self._pumps:
            with suppress(asyncio.CancelledError):
                await t
        self._pumps.clear()
        self.logger.info("All pumps stopped")

    def stop_pumps_request(self) -> None:
        """Запросить остановку насосов (без ожидания)."""
        for t in self._pumps:
            t.cancel()

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