"""
TaskRouter — маршрутизация задач между каналами слота.
"""
import asyncio
import logging
from contextlib import suppress
from typing import Callable

from host.app.transports.base import Transport, TransportDead
from host.app.slots.exceptions import AllChannelsFailed, PendingTask


class TaskRouter:
    """Маршрутизатор с WS-приоритетом и Serial fallback."""

    def __init__(
        self,
        slot_id: str,
        transports: list[Transport],
        set_offline: Callable[[str], None],
        pending_provider: Callable[[], dict[str, PendingTask]],
    ) -> None:
        self.slot_id = slot_id
        self.transports = transports
        self._set_offline = set_offline
        self._pending_provider = pending_provider
        self._pumps: list[asyncio.Task] = []
        self.logger = logging.getLogger(f"slot_{slot_id}")

    def _get_transports_by_priority(self) -> list[Transport]:
        """WS первым, затем остальные; доступность проверяется при dispatch."""
        return sorted(self.transports, key=lambda ch: 0 if ch.name.lower() in ("ws", "websocket") else 1)

    async def route(self, payload: dict, record: PendingTask) -> dict:
        """Отправить задачу по первому доступному каналу.

        После успешной отправки не повторяет задачу на другом канале:
        потеря отчёта не доказывает, что тест не был выполнен.
        """
        tried: list[str] = []
        last_err: Exception | None = None

        for transport in self._get_transports_by_priority():
            if not transport.is_alive or not transport.is_available:
                continue
            tried.append(transport.name)
            try:
                await transport.send(payload)
            except TransportDead as exc:
                last_err = exc
                self.logger.warning("send %s failed: %s", transport.name, exc)
                continue

            self.logger.info("task %s -> %s", record.task_id, transport.name)
            try:
                report = await self._wait_report(record, transport)
            except (asyncio.TimeoutError, TransportDead) as exc:
                last_err = exc
                self.logger.error(
                    "task %s dispatched via %s but final report was not received; "
                    "not retrying on another channel to avoid duplicate execution",
                    record.task_id, transport.name,
                )
                break

            self.logger.info(
                "task %s <- %s (%s)", record.task_id, transport.name,
                report.get("status"),
            )
            return report

        if not any(ch.is_alive and ch.is_available for ch in self.transports):
            self._set_offline("All transports failed or unavailable")
        raise AllChannelsFailed(self.slot_id, record.task_id, tried, last_err)

    async def _wait_report(self, record: PendingTask, ch: Transport) -> dict:
        """Ждать финальный отчёт, продлевая таймаут при progress."""
        while True:
            record.progress.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.final), timeout=record.progress_timeout,
                )
            except asyncio.TimeoutError:
                if not ch.is_alive:
                    raise TransportDead(f"{ch.name} died while waiting {record.task_id}")
                if not record.progress.is_set():
                    raise
                continue
            return record.final.result()

    def start_pumps(self) -> None:
        """Запустить чтение сообщений каналов."""
        self.stop_pumps_request()
        pending = self._pending_provider()
        self._pumps = [
            asyncio.create_task(self.pump(ch, pending), name=f"pump-{self.slot_id}-{ch.name}")
            for ch in self.transports
        ]
        self.logger.info("Started %d pumps", len(self._pumps))

    async def stop_pumps(self) -> None:
        """Остановить и дождаться насосов."""
        for task in self._pumps:
            task.cancel()
        for task in self._pumps:
            with suppress(asyncio.CancelledError):
                await task
        self._pumps.clear()
        self.logger.info("All pumps stopped")

    def stop_pumps_request(self) -> None:
        """Запросить остановку насосов."""
        for task in self._pumps:
            task.cancel()

    async def pump(self, ch: Transport, pending: dict[str, PendingTask]) -> None:
        """Распределять входящие report/error по ожидающим задачам."""
        try:
            async for msg in ch.incoming():
                msg_type = msg.get("type")
                if msg_type in ("channel_busy", "channel_free"):
                    target_name = msg.get("channel", ch.name)
                    target = next(
                        (item for item in self.transports if item.name == target_name),
                        ch,
                    )
                    if msg_type == "channel_busy":
                        target.mark_for_testing()
                    else:
                        target.release_from_testing()
                    continue
                if msg_type not in ("report", "error"):
                    continue
                task_id = msg.get("task_id")
                if not task_id:
                    continue
                record = pending.get(task_id)
                if record is not None:
                    record.on_report(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.error("pump %s is broken: %s", ch.name, exc)
