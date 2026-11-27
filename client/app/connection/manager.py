"""
ClientManager — бизнес-логика клиента.
"""
import asyncio
import logging
from typing import Any

from client.app.connection.events import ClientEvent
from client.app.tests import TestRegistry, create_default_registry

logger = logging.getLogger(__name__)


class ClientManager:
    """Обработчик задач с защитой от повторного исполнения task_id."""

    def __init__(self, client, test_registry: TestRegistry | None = None):
        self.client = client
        self.test_registry = test_registry or create_default_registry()
        self._task_lock = asyncio.Lock()
        self._running: set[str] = set()
        self._completed: dict[str, dict] = {}

        self.client.subscribe(ClientEvent.MESSAGE_RECEIVED, self._on_message)
        self.client.subscribe(ClientEvent.CONNECTED, self._on_connected)
        self.client.subscribe(ClientEvent.DISCONNECTED, self._on_disconnected)

    async def _on_connected(self, _event, _data):
        logger.info("[ClientManager] Connected to server")

    async def _on_disconnected(self, _event, _data):
        logger.info("[ClientManager] Disconnected from server")

    async def _on_message(self, _event, data: Any):
        if not isinstance(data, dict):
            return
        if data.get("type") == "task":
            await self._process_task(data)
        else:
            logger.debug("[ClientManager] Unknown message type: %s", data.get("type"))

    async def _process_task(self, msg: dict) -> None:
        from app.messages import Task, Report, TaskStatus

        try:
            task = Task.from_dict(msg)
        except (KeyError, ValueError) as exc:
            logger.error("[ClientManager] Invalid task format: %s, msg=%s", exc, msg)
            return

        async with self._task_lock:
            if task.task_id in self._completed:
                cached = self._completed[task.task_id]
                await self.client.send_json(cached)
                logger.warning("Replayed cached result for duplicate task %s", task.task_id)
                return
            if task.task_id in self._running:
                logger.warning("Ignoring concurrent duplicate task %s", task.task_id)
                return
            self._running.add(task.task_id)

        affected_channel = "serial" if task.command == "serial-benchmark" else (
            "ws" if task.command == "lan-benchmark" else None
        )
        try:
            if affected_channel:
                await self.client.send_json({"type": "channel_busy", "channel": affected_channel})
            logger.info("Task %s: command=%s params=%s", task.task_id, task.command, task.params)
            test = self.test_registry.get(task.command)

            async def report_cb(report: dict) -> None:
                report["task_id"] = task.task_id
                report["command"] = task.command
                await self.client.send_json(report)

            result = await test.run(task.params, report_cb)
            final_report = Report(
                task_id=task.task_id,
                command=task.command,
                status=TaskStatus.SUCCESS,
                result=result,
            ).to_dict()
        except ValueError as exc:
            final_report = Report(
                task_id=task.task_id,
                command=task.command,
                status=TaskStatus.ERROR,
                error=str(exc),
            ).to_dict()
            logger.error("Task %s unknown command/error: %s", task.task_id, exc)
        except Exception as exc:
            final_report = Report(
                task_id=task.task_id,
                command=task.command,
                status=TaskStatus.ERROR,
                error=str(exc),
            ).to_dict()
            logger.exception("Task %s failed", task.task_id)
        finally:
            if affected_channel:
                try:
                    await self.client.send_json({"type": "channel_free", "channel": affected_channel})
                except Exception:
                    logger.warning("Could not send channel_free for %s", affected_channel)

        async with self._task_lock:
            self._running.discard(task.task_id)
            self._completed[task.task_id] = final_report
            # Bounded result cache to avoid unbounded memory growth.
            if len(self._completed) > 256:
                self._completed.pop(next(iter(self._completed)))

        await self.client.send_json(final_report)
        logger.info("Task %s completed (%s)", task.task_id, final_report.get("status"))
