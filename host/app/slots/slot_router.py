"""Routes tasks across a slot's configured channels."""
import asyncio
import logging
from contextlib import suppress
from typing import Callable

from host.app.channels.base import Channel, ChannelDead
from host.app.slots.exceptions import AllChannelsFailed, PendingTask


class TaskRouter:
    def __init__(
        self,
        slot_id: str,
        channels: list[Channel],
        set_offline: Callable[[str], None],
        pending_provider: Callable[[], dict[str, PendingTask]],
    ) -> None:
        self.slot_id = slot_id
        self.channels = channels
        self._set_offline = set_offline
        self._pending_provider = pending_provider
        self._pumps: list[asyncio.Task] = []
        self.logger = logging.getLogger(f"slot_{slot_id}")

    async def route(self, payload: dict, record: PendingTask) -> dict:
        tried: list[str] = []
        last_err: Exception | None = None

        for channel in self.channels:
            if not channel.is_alive:
                continue
            tried.append(channel.name)
            try:
                await channel.send(payload)
                self.logger.info("Task %s sent through %s", record.task_id, channel.name)
            except ChannelDead as exc:
                last_err = exc
                self.logger.warning("Send through %s failed: %s", channel.name, exc)
                continue
            try:
                report = await self._wait_report(record, channel)
                self.logger.info(
                    "Task %s received through %s (%s)",
                    record.task_id,
                    channel.name,
                    report.get("status"),
                )
                return report
            except asyncio.TimeoutError as exc:
                last_err = exc
                self.logger.warning("Task %s timed out on %s; trying next channel", record.task_id, channel.name)
            except ChannelDead as exc:
                last_err = exc
                self.logger.warning("Channel %s died during task %s", channel.name, record.task_id)

        if not any(channel.is_alive for channel in self.channels):
            self._set_offline("All channels failed")
        raise AllChannelsFailed(self.slot_id, record.task_id, tried, last_err)

    async def _wait_report(self, record: PendingTask, channel: Channel) -> dict:
        while True:
            record.progress.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.final),
                    timeout=record.progress_timeout,
                )
            except asyncio.TimeoutError:
                if not channel.is_alive:
                    raise ChannelDead(
                        f"{channel.name} died while waiting for task {record.task_id}"
                    )
                if not record.progress.is_set():
                    raise
                continue
            return record.final.result()

    async def start_pumps(self) -> None:
        """Replace existing readers only after every old pump has stopped."""
        await self.stop_pumps()
        pending = self._pending_provider()
        self._pumps = [
            asyncio.create_task(
                self.pump(channel, pending),
                name=f"pump-{self.slot_id}-{channel.name}",
            )
            for channel in self.channels
        ]
        self.logger.info("Started %d channel pumps", len(self._pumps))

    async def stop_pumps(self) -> None:
        tasks, self._pumps = self._pumps, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.logger.info("All channel pumps stopped")

    async def pump(self, channel: Channel, pending: dict[str, PendingTask]) -> None:
        try:
            async for message in channel.incoming():
                if message.get("type") not in ("report", "error"):
                    continue
                task_id = message.get("task_id")
                if not task_id:
                    continue
                record = pending.get(task_id)
                if record is not None:
                    record.on_report(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("Channel pump %s failed", channel.name)
