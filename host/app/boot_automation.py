"""
BootAutomation — автологин через COM1.
Подписывается на паттерны login/password и шлёт данные.
Держит флаг is_logged_in, который сбрасывается при reboot/power_on.
"""
import asyncio
import logging

from host.app.log_watcher import LogWatcher


class BootAutomation:
    def __init__(
            self,
            watcher: LogWatcher,
            login: str,
            password: str,
            success_patterns: list[str] | None = None,
    ):
        self.watcher = watcher
        self.slot_id = watcher.slot_id
        self.login = login
        self.password = password
        self.success_patterns = [
            p.lower() for p in (success_patterns or ["~", "$", "# "])
        ]

        self.is_logged_in = False
        self._auth_sent = False
        self._lock = asyncio.Lock()

        self.logger = logging.getLogger(f"slot_{watcher.slot_id}")

        watcher.register_rule(["login:"], self._on_login, name="boot-login")
        watcher.register_rule(["password:"], self._on_password, name="boot-password")
        watcher.register_rule(
            ["login:", "password:"], self._on_new_auth_cycle,
            name="boot-reset-cycle",
        )
        watcher.register_rule(
            self.success_patterns, self._on_shell_prompt,
            name="boot-shell",
        )

    async def _on_new_auth_cycle(self, line: str) -> None:
        """Сброс флагов при приглашении входа в систему"""
        if "login:" in line.lower():
            self._auth_sent = False
            self.is_logged_in = False

    async def _on_login(self, line: str) -> None:
        """Отправка логина из файла настроек"""
        async with self._lock:
            if self._auth_sent or self.is_logged_in:
                return
            self.logger.info("a system 'login prompt' appeared -> send 'login'")
            await self.watcher.send_line(self.login)
            self._auth_sent = True

    async def _on_password(self, line: str) -> None:
        """Отправка пароля из файла настроек"""
        self.logger.info("a 'password prompt' appeared -> send 'password'")
        await self.watcher.send_line(self.password)

    async def _on_shell_prompt(self, line: str) -> None:
        """Смена флагов состояния при получении паттерна приглашения ввода команд"""
        if not self.is_logged_in:
            # LogLevel.SUCCESS заменён на INFO
            self.logger.info("a 'shell prompt' appeared, authorized already")
            self.is_logged_in = True

    def reset(self) -> None:
        """Сброс при reboot/power_on."""
        self.logger.debug("reset authorization state")
        self.is_logged_in = False
        self._auth_sent = False
