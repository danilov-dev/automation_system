from textual.app import ComposeResult
from textual.containers import Container, Grid, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label

from host.app.slot import Slot


class MiniSlotCard(Widget):
    class IconClicked(Message):
        pass

    def __init__(self, slot: Slot, **kwargs):
        super().__init__(**kwargs)
        self.slot = slot

    def on_icon_clicked(self, event: IconClicked):
        self.app.notify(f"I'm from method for {self.slot.name}")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f'[@click=app.show_slot_info("{self.slot.name}")]◦◦◦[/]',
                markup=True,
                classes="link-btn",
            )
            yield Label(self.slot.name, classes="title")
            with Grid(classes="cell-grid"):
                yield Container(Label("bios", id="bios"), classes="cell")
                yield Container(Label("os", id="os"), classes="cell")
                yield Container(Label("ws", id="ws"), classes="cell")
                yield Container(Label("shell", id="shell"), classes="cell")
                yield Container(Label("serial_port", id="serial_port"), classes="cell")
                yield Container(Label(f"{self.slot.v} V", id="v"), classes="cell")
                yield Container(Label(f"{self.slot.a} A", id="a"), classes="cell")
                yield Container(Label(f"{self.slot.wt} Wt", id="wt"), classes="cell")
                yield Container(Label(f"step: {self.slot.step}", id="step"), classes="cell")
                yield Container(Label(f"time:\n{self.slot.test_time}", id="test_time"), classes="cell")
            yield Label(f"STATE: {self.slot.state.name}", id="state", classes="title")

    def _get_field_value(self, field_name: str):
        """Получить значение поля из правильного места."""
        # Датчики и состояние теста — из слота
        if field_name in ("v", "a", "wt", "step", "test_time"):
            return getattr(self.slot, field_name)
        # Каналы — из слота
        elif field_name == "ws":
            return self.slot.ws_alive
        elif field_name == "serial_port":
            return self.slot.serial_alive
        # Наблюдаемое состояние платы — из платы
        elif field_name in ("bios", "os", "shell"):
            board = self.slot.board
            return getattr(board, field_name) if board else False
        return None

    def update_field(self, field_name: str) -> None:
        """Обновить один Label по имени поля."""
        value = self._get_field_value(field_name)
        if value is None:
            return
        try:
            label = self.query_one(f"#{field_name}", Label)
        except NoMatches:
            return

        if isinstance(value, bool):
            label.update(field_name)
            label.remove_class("enabled", "disabled")
            label.add_class("enabled" if value else "disabled")
        elif isinstance(value, (int, float)):
            suffix = {"v": " V", "a": " A", "wt": " Wt"}.get(field_name, "")
            prefix = "step: " if field_name == "step" else ""
            label.update(f"{prefix}{value}{suffix}")
            label.remove_class("has-value")
            if value != 0:
                label.add_class("has-value")
        elif field_name == "test_time":
            label.update(f"time:\n{value}")

    def update_state(self) -> None:
        """Обновить только STATE."""
        try:
            label = self.query_one("#state", Label)
            label.update(f"STATE: {self.slot.state.name}")
        except NoMatches:
            pass

    def update_all(self) -> None:
        """Обновить все поля."""
        for field in ("bios", "os", "ws", "shell", "serial_port",
                      "v", "a", "wt", "step", "test_time"):
            self.update_field(field)
        self.update_state()

    def on_mount(self) -> None:
        self.update_all()