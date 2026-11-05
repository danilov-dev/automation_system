from datetime import timedelta

from textual.app import ComposeResult
from textual.containers import Container, Grid, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label

from host.app.slots.slot import Slot


class MiniSlotCard(Widget):
    class IconClicked(Message):
        pass

    # ── Кэш виджетов (аннотации на уровне класса) ─────
    _lbl_bios: Label
    _lbl_os: Label
    _lbl_shell: Label
    _lbl_ws: Label
    _lbl_serial: Label
    _lbl_v: Label
    _lbl_a: Label
    _lbl_wt: Label
    _lbl_step: Label
    _lbl_time: Label
    _lbl_state: Label

    # ── Реактивные атрибуты ───────────────────────────
    bios = reactive(False)
    os = reactive(False)
    shell = reactive(False)
    ws_alive = reactive(False)
    serial_alive = reactive(False)
    v = reactive(0.0)
    a = reactive(0.0)
    wt = reactive(0.0)
    step = reactive(0)
    test_time = reactive(timedelta())
    state = reactive("IDLE")

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

    # ── Асинхронные колбэки (единообразно) ────────────
    async def _on_state(self, slot_id: str, old: str, new: str) -> None:
        self.state = new

    async def _on_metrics(self, slot_id: str, v: float, a: float, wt: float) -> None:
        self.v = v
        self.a = a
        self.wt = wt

    async def _on_board(self, slot_id: str, bios: bool, os: bool, shell: bool) -> None:
        self.bios = bios
        self.os = os
        self.shell = shell

    async def _on_progress(self, slot_id: str, step: int, test_time: timedelta) -> None:
        self.step = step
        self.test_time = test_time

    async def _on_channels(self, slot_id: str, ws: bool, serial: bool, log: bool) -> None:
        self.ws_alive = ws
        self.serial_alive = serial

    # ── Watch-методы ──────────────────────────────────
    def watch_bios(self, value: bool) -> None:
        self._set_bool(self._lbl_bios, "bios", value)

    def watch_os(self, value: bool) -> None:
        self._set_bool(self._lbl_os, "os", value)

    def watch_shell(self, value: bool) -> None:
        self._set_bool(self._lbl_shell, "shell", value)

    def watch_ws_alive(self, value: bool) -> None:
        self._set_bool(self._lbl_ws, "ws", value)

    def watch_serial_alive(self, value: bool) -> None:
        self._set_bool(self._lbl_serial, "serial_port", value)

    def watch_v(self, value: float) -> None:
        self._lbl_v.update(f"{round(value,3)} V")
        self._lbl_v.set_class(value > 12.1, "disabled")

    def watch_a(self, value: float) -> None:
        self._lbl_a.update(f"{round(value,3)} A")
        self._lbl_a.set_class(value > 0.92, "disabled")

    def watch_wt(self, value: float) -> None:
        self._lbl_wt.update(f"{round(value,3)} Wt")

    def watch_step(self, value: int) -> None:
        self._lbl_step.update(f"step: {value}")

    def watch_test_time(self, value: timedelta) -> None:
        self._lbl_time.update(f"time:\n{value}")

    def watch_state(self, value: str) -> None:
        self._lbl_state.update(f"STATE: {value}")

    def _set_bool(self, label: Label, name: str, value: bool) -> None:
        label.update(name)
        label.remove_class("enabled", "disabled")
        label.add_class("enabled" if value else "disabled")

    # ── Fallback-обновление ───────────────────────────
    def update_all(self) -> None:
        board = self.slot.board
        self.bios = board.bios if board else False
        self.os = board.os if board else False
        self.shell = board.shell if board else False
        self.ws_alive = self.slot.ws_alive
        self.serial_alive = self.slot.serial_alive
        self.v = self.slot.v
        self.a = self.slot.a
        self.wt = self.slot.wt
        self.step = self.slot.step
        self.test_time = self.slot.test_time
        self.state = self.slot.state.name

    def on_mount(self) -> None:
        # Кэшируем виджеты
        self._lbl_bios = self.query_one("#bios", Label)
        self._lbl_os = self.query_one("#os", Label)
        self._lbl_shell = self.query_one("#shell", Label)
        self._lbl_ws = self.query_one("#ws", Label)
        self._lbl_serial = self.query_one("#serial_port", Label)
        self._lbl_v = self.query_one("#v", Label)
        self._lbl_a = self.query_one("#a", Label)
        self._lbl_wt = self.query_one("#wt", Label)
        self._lbl_step = self.query_one("#step", Label)
        self._lbl_time = self.query_one("#test_time", Label)
        self._lbl_state = self.query_one("#state", Label)

        # Подписываемся на события слота
        self.slot.on_state_change(self._on_state)
        self.slot.on_metrics_change(self._on_metrics)
        self.slot.on_board_change(self._on_board)
        self.slot.on_progress_change(self._on_progress)
        self.slot.on_channels_change(self._on_channels)

        # Инициализация
        self.update_all()