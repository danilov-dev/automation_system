"""
PcSession — одна логическая сессия тестируемого ПК.

Держит два канала (primary=TCP, backup=Serial), роутит задачи и отчёты,
делает failover с сохранением task_id.

Не знает:
  - какой транспорт у primary/backup (только Channel);
  - как устроен TCP-сервер / COM-порт;
  - про TUI и FastAPI — это уровнем выше (Orchestrator).

Знает:
  - что отчёт должен прийти с тем же task_id;
  - что 'in_progress' — это не финал;
  - что при отказе одного канала пробуем второй с ТЕМ ЖЕ task_id.
"""
import asyncio
import uuid
from contextlib import suppress

from app.channels.base import Channel, ChannelDead, ChannelError
from app.utils.console_logger import ConsoleLogger, LogLevel


class AllChannelsFailed(ChannelError):
    """Ни один канал не смог доставить задачу и получить отчёт."""

    def __init__(
        self,
        pc_id: str,
        task_id: str,
        tried: list[str],
        last_err: Exception | None,
    ):
        msg = (
            f"[{pc_id}] task {task_id} не выполнен. "
            f"tried={tried}, last_err={last_err!r}"
        )
        super().__init__(msg)
        self.pc_id = pc_id
        self.task_id = task_id
        self.tried = tried
        self.last_err = last_err


class _PendingTask:
    """
    Состояние ожидания одного отчёта.
    final   — Future с финальным отчётом (report|error, не in_progress).
    progress— Event, взводится при 'in_progress'; используется для продления окна.
    """

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


class PcSession:
    def __init__(self, pc_id: str, primary: Channel, backup: Channel):
        self.pc_id = pc_id
        self.primary = primary
        self.backup = backup

        self._pending: dict[str, _PendingTask] = {}
        self._pumps: list[asyncio.Task] = []
        self._started = False

    @property
    def channels(self) -> list[Channel]:
        return [self.primary, self.backup]

    # ── lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for ch in self.channels:
            with suppress(Exception):
                await ch.start()
        self._pumps = [
            asyncio.create_task(
                self._pump(ch), name=f"pump-{self.pc_id}-{ch.name}"
            )
            for ch in self.channels
        ]
        ConsoleLogger.write(f"[{self.pc_id}] сессия запущена", LogLevel.INFO)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False

        # разбудить все ожидания
        for record in list(self._pending.values()):
            if not record.final.done():
                record.final.set_exception(
                    ChannelDead(f"[{self.pc_id}] session stopped")
                )
        self._pending.clear()

        for t in self._pumps:
            t.cancel()
        for t in self._pumps:
            with suppress(asyncio.CancelledError):
                await t
        self._pumps.clear()

        for ch in self.channels:
            with suppress(Exception):
                await ch.stop()
        ConsoleLogger.write(f"[{self.pc_id}] сессия остановлена", LogLevel.INFO)

    # ── публичное API ─────────────────────────────────────

    async def execute(
        self,
        command: str,
        params: dict | None = None,
        progress_timeout: float = 15.0,
    ) -> dict:
        """
        Отправить задачу и дождаться отчёта.

        progress_timeout — окно ожидания между 'in_progress'-сигналами.
        Каждый in_progress продлевает окно ещё на progress_timeout.
        Общий «жёсткий» таймаут задаётся снаружи через asyncio.wait_for(...).
        """
        task_id = uuid.uuid4().hex[:8]
        payload = {
            "type": "task",
            "task_id": task_id,
            "command": command,
            "params": params or {},
        }
        record = _PendingTask(task_id, progress_timeout)
        self._pending[task_id] = record
        try:
            return await self._route(payload, record)
        finally:
            self._pending.pop(task_id, None)

    # ── маршрутизация ─────────────────────────────────────

    async def _route(self, payload: dict, record: _PendingTask) -> dict:
        tried: list[str] = []
        last_err: Exception | None = None

        for ch in self.channels:
            if not ch.is_alive:
                continue
            tried.append(ch.name)

            try:
                await ch.send(payload)
                ConsoleLogger.write(
                    f"[{self.pc_id}] task {record.task_id} → {ch.name}",
                    LogLevel.INFO,
                )
            except ChannelDead as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.pc_id}] send {ch.name} fail: {e}",
                    LogLevel.WARNING,
                )
                continue

            try:
                report = await self._wait_report(record, ch)
                ConsoleLogger.write(
                    f"[{self.pc_id}] task {record.task_id} ← {ch.name} "
                    f"({report.get('status')})",
                    LogLevel.INFO,
                )
                return report
            except asyncio.TimeoutError as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.pc_id}] {ch.name} timeout по {record.task_id}, "
                    f"пробую резерв",
                    LogLevel.WARNING,
                )
                continue
            except ChannelDead as e:
                last_err = e
                ConsoleLogger.write(
                    f"[{self.pc_id}] {ch.name} умер в процессе "
                    f"{record.task_id}, пробую резерв",
                    LogLevel.WARNING,
                )
                continue

        raise AllChannelsFailed(self.pc_id, record.task_id, tried, last_err)

    async def _wait_report(self, record: _PendingTask, ch: Channel) -> dict:
        """
        Ждём финальный отчёт. Каждый in_progress продлевает окно.
        Если канал умер — ChannelDead, вызывающий пойдёт на резерв.
        """
        while True:
            record.progress.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.final),
                    timeout=record.progress_timeout,
                )
            except asyncio.TimeoutError:
                # порядок важен: сначала «жив ли канал», потом «был ли прогресс»
                if not ch.is_alive:
                    raise ChannelDead(
                        f"{ch.name} died while waiting {record.task_id}"
                    )
                if not record.progress.is_set():
                    raise
                continue
            return record.final.result()

    # ── pump ──────────────────────────────────────────────

    async def _pump(self, ch: Channel) -> None:
        try:
            async for msg in ch.incoming():
                mtype = msg.get("type")
                if mtype not in ("report", "error"):
                    continue
                tid = msg.get("task_id")
                if not tid:
                    continue
                record = self._pending.get(tid)
                if record is None:
                    continue
                record.on_report(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            ConsoleLogger.write(
                f"[{self.pc_id}] pump {ch.name} упал: {e}",
                LogLevel.ERROR,
            )