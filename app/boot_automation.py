"""
BootAutomation — автологин через COM1.

Подписывается на паттерны login/password и шлёт данные.
Держит флаг is_logged_in, который сбрасывается при reboot/power_on.

Не знает про Channel/PcSession. Просто реакция на строки.
"""
import asyncio

from app.log_watcher import LogWatcher
from app.utils.console_logger import ConsoleLogger, LogLevel


class BootAutomation:
    def __init__(
        self,
        watcher: LogWatcher,
        login: str,
        password: str,
        success_patterns: list[str] | None = None,
    ):
        self.watcher = watcher
        self.login = login
        self.password = password
        self.success_patterns = [
            p.lower() for p in (success_patterns or ["~ ", "$ ", "# "])
        ]

        self.is_logged_in = False
        self._auth_sent = False
        self._lock = asyncio.Lock()

        watcher.on(["login:"], self._on_login, name="boot-login")
        watcher.on(["password:"], self._on_password, name="boot-password")
        watcher.on(["login:", "password:"], self._on_new_auth_cycle,
                   name="boot-reset-cycle")
        watcher.on(self.success_patterns, self._on_shell_prompt,
                   name="boot-shell")

    async def _on_new_auth_cycle(self, line: str) -> None:
        """Если появился login: — значит новая авторизация, сбрасываем флаг."""
        if "login:" in line.lower():
            self._auth_sent = False
            self.is_logged_in = False

    async def _on_login(self, line: str) -> None:
        async with self._lock:
            if self._auth_sent or self.is_logged_in:
                return
            ConsoleLogger.write(
                f"[{self.watcher.pc_id}] login prompt → отправляю логин",
                LogLevel.INFO,
            )
            await self.watcher.send_line(self.login)
            self._auth_sent = True

    async def _on_password(self, line: str) -> None:
        ConsoleLogger.write(
            f"[{self.watcher.pc_id}] password prompt → отправляю пароль",
            LogLevel.INFO,
        )
        await self.watcher.send_line(self.password)

    async def _on_shell_prompt(self, line: str) -> None:
        if not self.is_logged_in:
            ConsoleLogger.write(
                f"[{self.watcher.pc_id}] shell prompt получен, авторизованы",
                LogLevel.SUCCESS,
            )
            self.is_logged_in = True

    def reset(self) -> None:
        """Сброс при reboot/power_on."""
        self.is_logged_in = False
        self._auth_sent = False