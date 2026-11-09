"""
SlotTestRunner — сессия тестирования одного слота.

Отвечает за:
— очередь и последовательное выполнение тестов на этом слоте
— владение pending-задачами (dict[task_id, PendingTask])
— тайм-ауты прогресса, отмену сессии
— сбор TestResult, сохранение через ResultStorage
— уведомление контроллера состояния о ходе сессии
— уведомление подписчиков о результатах

НЕ отвечает за:
— транспорт и фейловер (это TaskRouter)
— матрицу переходов состояния (это SlotStateController)
— lifecycle слота (это Slot)
— другие слоты (это SlotManager)
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Callable, Awaitable, Union, Optional

from host.app.slots.exceptions import PendingTask
from host.app.slots.slot_router import TaskRouter
from host.app.slots.state_controller import SlotStateController
from host.app.testing.test_result import TestResult
from host.app.testing.storage import ResultStorage, NullResultStorage

ResultCallback = Callable[[TestResult], Union[None, Awaitable[None]]]


class SlotTestRunner:
    def __init__(
        self,
        slot_id: str,
        router: TaskRouter,
        controller: SlotStateController,
        storage: Optional[ResultStorage] = None,
        progress_timeout: float = 30.0,
    ):
        self.slot_id = slot_id
        self._router = router
        self._controller = controller
        self._storage = storage or NullResultStorage()
        self.progress_timeout = progress_timeout

        self._pending: dict[str, PendingTask] = {}
        self._lock = asyncio.Lock()
        self._session_task: Optional[asyncio.Task] = None
        self._cancel_requested = False
        self._current_command: Optional[str] = None
        self._result_callbacks: list[ResultCallback] = []
        self.logger = logging.getLogger(f"slot_{slot_id}")

    # ── Свойства ────────────────────────────────────────
    @property
    def pending(self) -> dict[str, PendingTask]:
        """Отдаётся роутеру для pump()."""
        return self._pending

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    @property
    def current_command(self) -> Optional[str]:
        return self._current_command

    # ── Подписка ────────────────────────────────────────
    def on_result(self, cb: ResultCallback) -> None:
        self._result_callbacks.append(cb)

    # ── Публичное API ───────────────────────────────────
    async def run_one(
        self,
        command: str,
        params: Optional[dict] = None,
        progress_timeout: Optional[float] = None,
    ) -> TestResult:
        """Выполнить один тест. Сериализуется через lock."""
        async with self._lock:
            return await self._execute(command, params or {}, progress_timeout)

    async def run_suite(
        self,
        commands: list[str],
        params: Optional[dict] = None,
        stop_on_error: bool = True,
    ) -> list[TestResult]:
        """
        Выполнить набор тестов последовательно.
        Сессия целиком держит lock — никто не влезет между тестами.
        """
        results: list[TestResult] = []
        self._cancel_requested = False

        async with self._lock:
            self._controller.on_session_started(len(commands))
            try:
                for command in commands:
                    if self._cancel_requested:
                        results.append(self._skipped(command, "session cancelled"))
                        continue

                    if not self._controller.is_ready and not self._controller.is_testing:
                        results.append(self._skipped(
                            command, f"slot not ready: {self._controller.state.name}"
                        ))
                        if stop_on_error:
                            break
                        continue

                    result = await self._execute(command, params or {}, None)
                    results.append(result)

                    if not result.ok and stop_on_error:
                        self.logger.warning(
                            f"[runner] suite stopped on {command}: {result.status}"
                        )
                        break
            finally:
                self._controller.on_session_finished()
                self._current_command = None

        return results

    def cancel(self) -> None:
        """Запросить отмену сессии. Текущий тест будет прерван."""
        self._cancel_requested = True
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(
                    asyncio.CancelledError(f"[{self.slot_id}] test session cancelled")
                )

    def abort(self, reason: str) -> None:
        """
        Аварийный сброс: слот остановлен / питание пропало.
        Будит все ожидающие задачи, чистит pending.
        """
        self._cancel_requested = True
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(RuntimeError(reason))
        self._pending.clear()
        self._current_command = None

    # ── Внутреннее ──────────────────────────────────────
    async def _execute(
        self,
        command: str,
        params: dict,
        progress_timeout: Optional[float],
    ) -> TestResult:
        task_id = uuid.uuid4().hex[:8]
        result = TestResult(
            slot_id=self.slot_id,
            command=command,
            task_id=task_id,
            params=params,
            status="pending",
        )
        self._current_command = command

        record = PendingTask(
            task_id,
            progress_timeout or self.progress_timeout,
            on_progress=lambda msg: self._controller.apply_progress(msg),
        )
        self._pending[task_id] = record
        self._controller.on_task_started(command)

        payload = {
            "type": "task",
            "task_id": task_id,
            "command": command,
            "params": params,
        }
        self.logger.info(f"[runner] {command} ({task_id}) start")

        try:
            report = await self._router.route(payload, record)
            result.status = report.get("status", "success")
            result.result = report.get("result", {}) or {}
            result.error = report.get("error")
            self._controller.apply_report(report)

        except asyncio.TimeoutError:
            result.status = "timeout"
            result.error = f"no progress for {record.progress_timeout}s"
            self.logger.warning(f"[runner] {command} ({task_id}) timeout")

        except asyncio.CancelledError:
            result.status = "cancelled"
            result.error = "cancelled"
            raise_ = False  # сессия отменена — не роняем слот

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            self.logger.error(f"[runner] {command} ({task_id}) failed: {e}")
            self._controller.on_task_failed(str(e))

        finally:
            result.finished_at = datetime.now()
            self._pending.pop(task_id, None)
            self._current_command = None
            self._controller.on_task_finished()
            self._storage.save(result)
            await self._emit_result(result)
            self.logger.info(
                f"[runner] {command} ({task_id}) -> {result.status} "
                f"({result.duration_s:.1f}s)"
            )

        return result

    def _skipped(self, command: str, reason: str) -> TestResult:
        r = TestResult(
            slot_id=self.slot_id,
            command=command,
            status="skipped",
            error=reason,
            finished_at=datetime.now(),
        )
        self._storage.save(r)
        return r

    async def _emit_result(self, result: TestResult) -> None:
        for cb in self._result_callbacks:
            try:
                res = cb(result)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                self.logger.error(f"[runner] result cb error: {e}")