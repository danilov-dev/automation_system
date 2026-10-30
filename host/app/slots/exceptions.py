"""Исключения и типы данных для слота."""
import asyncio
from host.app.channels.base import ChannelError


class AllChannelsFailed(ChannelError):
    """Ни один канал не смог доставить задачу и получить отчёт."""

    def __init__(
        self,
        slot_id: str,
        task_id: str,
        tried: list[str],
        last_err: Exception | None,
    ):
        msg = (
            f"[{slot_id}] task {task_id} не выполнен. "
            f"tried={tried}, last_err={last_err!r}"
        )
        super().__init__(msg)
        self.slot_id = slot_id
        self.task_id = task_id
        self.tried = tried
        self.last_err = last_err


class PendingTask:
    """Состояние ожидания одного отчёта."""

    __slots__ = ("task_id", "progress_timeout", "final", "progress")

    def __init__(self, task_id: str, progress_timeout: float):
        self.task_id = task_id
        self.progress_timeout = progress_timeout
        self.final: asyncio.Future = asyncio.get_running_loop().create_future()
        self.progress = asyncio.Event()

    def on_report(self, msg: dict) -> None:
        if msg.get("status") == "in_progress":
            self.progress.set()
            return
        if not self.final.done():
            self.final.set_result(msg)