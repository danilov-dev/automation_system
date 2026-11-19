"""
Исключения и вспомогательные классы для работы со слотами.
"""
import asyncio
import logging
from typing import Optional, Callable
from host.app.messages import Report, TaskStatus

logger = logging.getLogger(__name__)


class ChannelDead(Exception):
    """Канал мёртв и не может отправить/получить данные."""
    pass


class AllChannelsFailed(Exception):

    def __init__(self, slot_id: str, task_id: str, tried: list[str], last_err: Optional[Exception]):
        self.slot_id = slot_id
        self.task_id = task_id
        self.tried = tried
        self.last_err = last_err
        super().__init__(
            f"All channels failed for slot {slot_id}, task {task_id}. "
            f"Tried: {tried}. Last error: {last_err}"
        )


class PendingTask:
    """
    Ожидающая задача: хранит Future для финального отчёта и Event для продления тайм-аута.
    ВНИМАНИЕ! Для создания экземпляра класса требуется запущенный event loop!

    Args:
        task_id (str): ID задачи
        progress_timeout (float | int): Тайм-аут ожидания
        on_progress (Optional[Callable[[dict], None]]): Callback функция для продления ожидания
    """

    def __init__(
            self,
            task_id: str,
            progress_timeout: float,
            on_progress: Optional[Callable[[dict], None]] = None,
    ):
        self.task_id = task_id
        self.progress_timeout = progress_timeout
        self.final: asyncio.Future = asyncio.get_running_loop().create_future()
        self.progress: asyncio.Event = asyncio.Event()
        self._on_progress = on_progress
        self.logger = logging.getLogger(f"task_{task_id}")

    def on_report(self, msg: dict) -> None:
        """
        Обрабатывает входящий отчет
        Args:
            msg (dict[Any, Any]): Отчет в виде словаря

        Returns: None

        """
        try:
            report = Report.from_dict(msg)
        except (KeyError, ValueError) as e:
            self.logger.error(f"[PendingTask] Invalid report format: {e}, msg={msg}")
            return

        self.logger.info(f"[PendingTask] Received report: status={report.status.value}")

        if report.status == TaskStatus.IN_PROGRESS:
            self.progress.set()
            if self._on_progress:
                try:
                    self._on_progress(msg)
                except Exception as e:
                    self.logger.error(f"[PendingTask] progress callback error: {e}")
            return

        if not self.final.done():
            self.logger.info(f"[PendingTask] Task {self.task_id} completed with status={report.status.value}")
            self.final.set_result(report.to_dict())
        else:
            self.logger.warning(f"[PendingTask] Task {self.task_id} final already done, ignoring")
