from textual.app import ComposeResult
from textual.containers import Horizontal, Container
from textual.widgets import Button, Label

class TopBar(Horizontal):
    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield Container(
                Label("Slots: 0 | Active: 0 | Reboot: 0", id="counters"),
                classes="label-container",
            )
            yield Button("▶", id="btn-start", variant="success")
            yield Button("■", id="btn-stop", variant="error")

    def update_counters(self, total: int, active: int, reboot: int) -> None:
        try:
            self.query_one("#counters").update(
                f"Slots: {total} | Active: {active} | Reboot: {reboot}"
            )
        except Exception:
            pass