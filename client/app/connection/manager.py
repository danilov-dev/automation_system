"""
ClientManager — бизнес-логика клиента.
"""
import logging
from typing import Any
from datetime import datetime
from client.app.connection.events import ClientEvent
from client.app.tests import TestRegistry, create_default_registry

logger = logging.getLogger(__name__)


class ClientManager:
    """Бизнес-логика клиента."""

    def __init__(self, client, test_registry: TestRegistry | None = None):
        self.client = client
        self.test_registry = test_registry or create_default_registry()

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

        msg_type = data.get("type")
        if msg_type == "task":
            await self._process_task(data)
        else:
            logger.debug(f"[ClientManager] Unknown message type: {msg_type}")

    async def _process_task(self, msg: dict):
        from app.messages import Task, Report, TaskStatus

        try:
            task = Task.from_dict(msg)
        except (KeyError, ValueError) as e:
            logger.error(f"[ClientManager] Invalid task format: {e}, msg={msg}")
            return

        logger.info(f"[ClientManager] Task {task.task_id}: command='{task.command}' params={task.params}")

        try:
            test = self.test_registry.get(task.command)

            async def report_cb(report: dict):
                report["task_id"] = task.task_id
                report["command"] = task.command
                await self.client.send_json(report)

            result = await test.run(task.params, report_cb)

            final_report = Report(
                task_id=task.task_id,
                command=task.command,
                status=TaskStatus.SUCCESS,
                result=result,
            )
            await self.client.send_json(final_report.to_dict())
            logger.info(f"[ClientManager] Task {task.task_id}: completed successfully")

        except ValueError as e:
            err_report = Report(
                task_id=task.task_id,
                command=task.command,
                status=TaskStatus.ERROR,
                error=str(e),
            )
            await self.client.send_json(err_report.to_dict())
            logger.error(f"[ClientManager] Task {task.task_id}: unknown command - {e}")

        except Exception as e:
            err_report = Report(
                task_id=task.task_id,
                command=task.command,
                status=TaskStatus.ERROR,
                error=str(e),
            )
            await self.client.send_json(err_report.to_dict())
            logger.error(f"[ClientManager] Task {task.task_id}: error - {e}")