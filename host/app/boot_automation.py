"""Automatic login over the console log watcher."""
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
    ) -> None:
        self.watcher = watcher
        self.slot_id = watcher.slot_id
        self.login = login
        self.password = password
        self.success_patterns = [
            pattern.lower() for pattern in (success_patterns or ["~", "$", "#"])
        ]
        self.is_logged_in = False
        self._auth_sent = False
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger(f"slot_{watcher.slot_id}")

        # Rule dispatch is ordered. Reset only after a successful prior login,
        # so repeated login prompts during one auth attempt don't resend credentials.
        watcher.register_rule(
            ["login:", "password:"],
            self._on_new_auth_cycle,
            name="boot-reset-cycle",
        )
        watcher.register_rule(["login:"], self._on_login, name="boot-login")
        watcher.register_rule(["password:"], self._on_password, name="boot-password")
        watcher.register_rule(
            self.success_patterns, self._on_shell_prompt, name="boot-shell"
        )

    async def _on_new_auth_cycle(self, line: str) -> None:
        if "login:" in line.lower() and self.is_logged_in:
            self.reset()

    async def _on_login(self, line: str) -> None:
        async with self._lock:
            if self._auth_sent or self.is_logged_in:
                return
            self.logger.info("Login prompt detected; sending configured login")
            self._auth_sent = await self.watcher.send_line(self.login)

    async def _on_password(self, line: str) -> None:
        if not await self.watcher.send_line(self.password):
            self.logger.warning("Password prompt detected while console is disconnected")

    async def _on_shell_prompt(self, line: str) -> None:
        if not self.is_logged_in:
            self.logger.info("Shell prompt detected; login completed")
            self.is_logged_in = True

    def begin_probe(self) -> tuple[bool, bool]:
        """Reset auth detection temporarily and return the state to restore on failure."""
        previous = (self.is_logged_in, self._auth_sent)
        self.reset()
        return previous

    def restore_state(self, state: tuple[bool, bool]) -> None:
        self.is_logged_in, self._auth_sent = state

    def reset(self) -> None:
        self.logger.debug("Resetting authorization state")
        self.is_logged_in = False
        self._auth_sent = False
