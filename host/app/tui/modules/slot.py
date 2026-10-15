from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, Label, Button
from textual import on


class MiniSlotCard(Static):
    def __init__(self, pc_id: str = "_", **kwargs):
        super().__init__(**kwargs)
        self.pc_id = pc_id
        self.mgr = None
        self.app_ref = None

    def compose(self) -> ComposeResult:
        yield Container(
            Label(f"PC-{self.pc_id}", classes="slot-title"),
            Label("WS:   ● ---", id=f"ws-{self.pc_id}"),
            Label("COM1: ● ---", id=f"com1-{self.pc_id}"),
            Label("COM2:  ● ---", id=f"com2-{self.pc_id}"),
            Label("Auth: ---", id=f"auth-{self.pc_id}"),
            Label("State: ---", id=f"state-{self.pc_id}"),
            classes="mini-slot-card",
        )

    def update_from_slot(self, status: dict) -> None:
        self._update_indicator(f"#ws-{self.pc_id}", "WS", status["ws_alive"])
        self._update_indicator(f"#com1-{self.pc_id}", "COM1", status["log_watcher_alive"])
        self._update_indicator(f"#com2-{self.pc_id}", "COM2", status["serial_alive"])

        auth_text = "✓ Logged in" if status["is_logged_in"] else "✗ Not logged"
        auth_widget = self.query_one(f"#auth-{self.pc_id}")
        auth_widget.update(f"Auth: {auth_text}")
        auth_widget.remove_class("ok", "fail")
        auth_widget.add_class("ok" if status["is_logged_in"] else "fail")

        self._update_state(f"#state-{self.pc_id}", status["state"])

    def _update_indicator(self, widget_id: str, name: str, alive: bool) -> None:
        widget = self.query_one(widget_id)
        text = "● ONLINE" if alive else "● OFFLINE"
        widget.update(f"{name}: {text}")
        widget.remove_class("ok", "fail")
        widget.add_class("ok" if alive else "fail")

    def _update_state(self, widget_id: str, state: str) -> None:
        widget = self.query_one(widget_id)
        widget.update(f"State: {state}")
        widget.remove_class("ok", "warn", "fail")
        state_lower = state.lower()
        if state_lower in ("idle", "ready", "power_on"):
            widget.add_class("ok")
        elif state_lower in ("reboot", "rebooting", "shutdown", "testing"):
            widget.add_class("warn")
        elif state_lower in ("error", "offline"):
            widget.add_class("fail")


class SlotCard(Static):
    def __init__(self, pc_id: str = "_", **kwargs):
        super().__init__(**kwargs)
        self.pc_id = pc_id
        self.mgr = None
        self.app_ref = None
        self._configured = False

    def compose(self) -> ComposeResult:
        yield Container(Label(f"🖥 {self.pc_id}", classes="slot-title"))
        yield Container(
            Label("WS:    ● ---", id=f"ws-{self.pc_id}"),
            Label("COM1:  ● ---", id=f"com1-{self.pc_id}"),
            Label("COM2:  ● ---", id=f"com2-{self.pc_id}"),
            Label("Auth:  ---", id=f"auth-{self.pc_id}"),
            Label("State: ---", id=f"state-{self.pc_id}"),
            classes="slot-data",
        )
        with Horizontal(classes="slot-actions"):
            yield Button("Ping", id=f"ping-{self.pc_id}", variant="primary")
            yield Button("Reboot", id=f"reboot-{self.pc_id}", variant="warning")
            yield Button("Info", id=f"info-{self.pc_id}", variant="default")

    def set_configured(self, configured: bool) -> None:
        """Включить/выключить кнопки в зависимости от конфигурации."""
        self._configured = configured
        for btn in self.query(Button):
            btn.disabled = not configured

    def update_from_slot(self, status: dict) -> None:
        self._update_indicator(f"#ws-{self.pc_id}", "WS", status["ws_alive"])
        self._update_indicator(f"#com1-{self.pc_id}", "COM1", status["log_watcher_alive"])
        self._update_indicator(f"#com2-{self.pc_id}", "COM2", status["serial_alive"])

        auth_text = "✓ Logged in" if status["is_logged_in"] else "✗ Not logged"
        auth_widget = self.query_one(f"#auth-{self.pc_id}")
        auth_widget.update(f"Auth: {auth_text}")
        auth_widget.remove_class("ok", "fail")
        auth_widget.add_class("ok" if status["is_logged_in"] else "fail")

        self._update_state(f"#state-{self.pc_id}", status["state"])

    def _update_indicator(self, widget_id: str, name: str, alive: bool) -> None:
        widget = self.query_one(widget_id)
        text = "● ONLINE" if alive else "● OFFLINE"
        widget.update(f"{name}: {text}")
        widget.remove_class("ok", "fail")
        widget.add_class("ok" if alive else "fail")

    def _update_state(self, widget_id: str, state: str) -> None:
        widget = self.query_one(widget_id)
        widget.update(f"State: {state}")
        widget.remove_class("ok", "warn", "fail")
        state_lower = state.lower()
        if state_lower in ("idle", "ready", "power_on"):
            widget.add_class("ok")
        elif state_lower in ("reboot", "rebooting", "shutdown", "testing"):
            widget.add_class("warn")
        elif state_lower in ("error", "offline"):
            widget.add_class("fail")

    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        if not self._configured or not self.mgr or not self.app_ref:
            return
        btn_id = event.button.id
        if btn_id == f"ping-{self.pc_id}":
            self.app_ref.run_worker(self._run_ping(), exclusive=True)
        elif btn_id == f"reboot-{self.pc_id}":
            self.app_ref.run_worker(self._run_reboot(), exclusive=True)
        elif btn_id == f"info-{self.pc_id}":
            self.app_ref.run_worker(self._run_info(), exclusive=True)

    async def _run_ping(self):
        try:
            self.query_one(f"#state-{self.pc_id}").update("State: PING...")
            slot = self.mgr.get_slot(self.pc_id)
            if slot is None:
                raise KeyError(f"slot {self.pc_id!r} not found")
            result = await slot.execute("ping", {"target": "8.8.8.8", "count": 2})
            status = result.get("status", "unknown")
            self.query_one(f"#state-{self.pc_id}").update(f"State: PING [{status}]")
            self.app_ref.notify(f"[{self.pc_id}] Ping result: {status}")
        except Exception as e:
            self.query_one(f"#state-{self.pc_id}").update("State: ERROR")
            self.app_ref.notify(f"[{self.pc_id}] Ping error: {e}", severity="error")

    async def _run_reboot(self):
        try:
            self.query_one(f"#state-{self.pc_id}").update("State: REBOOTING...")
            slot = self.mgr.get_slot(self.pc_id)
            if slot is None:
                raise KeyError(f"slot {self.pc_id!r} not found")
            await slot.execute("reboot", {})
            self.app_ref.notify(f"[{self.pc_id}] Reboot command sent")
        except Exception as e:
            self.query_one(f"#state-{self.pc_id}").update("State: ERROR")
            self.app_ref.notify(f"[{self.pc_id}] Reboot error: {e}", severity="error")

    async def _run_info(self):
        slot = self.mgr.get_slot(self.pc_id)
        if slot is None:
            return
        try:
            ws_port = self.app_ref.reg.router.actual_port
        except Exception:
            ws_port = self.app_ref.reg.cfg.ws.port

        log_port = getattr(slot.log_watcher, "port", None)
        ctrl_port = getattr(slot.serial_channel, "port", None)

        info_lines = [
            f"PC ID: {slot.pc_id}",
            f"State: {slot.state.name}",
            f"WS: ws://{self.app_ref.reg.cfg.ws.host}:{ws_port}",
            f"COM1 (log): {log_port}",
            f"COM2 (ctrl): {ctrl_port}",
            f"Logged in: {slot.boot_automation.is_logged_in}",
        ]
        self.app_ref.notify(" | ".join(info_lines), title=f"[{self.pc_id}] INFO")