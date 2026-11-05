import asyncio
import logging
from datetime import timedelta

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Grid, VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    ListView, ListItem, Label, SelectionList, Button,
    Static, ProgressBar, Sparkline, RichLog, Placeholder, Digits,
)

from .mini_slot_card import MiniSlotCard
from host.app.slots.slot import Slot, SlotState


class SlotLargeCard(Widget):
    DEFAULT_CSS = """
    SlotLargeCard .panel { display: none; }
    SlotLargeCard .panel.visible { display: block; }
    SlotLargeCard #dashboard { height: 1fr; padding: 1; }
    SlotLargeCard #metrics-grid { height: 7; grid-size: 3 4; grid-gutter: 1; }
    SlotLargeCard .metric { height: 3; border: solid $primary; content-align: center middle; text-style: bold; }
    SlotLargeCard .metric-value { color: $success; }
    SlotLargeCard #status-row { height: 3; padding: 0 1; }
    SlotLargeCard .status-flag { margin: 0 1; }
    SlotLargeCard .status-flag.ok { color: $success; }
    SlotLargeCard .status-flag.off { color: $error; }
    SlotLargeCard #progress-section { height: 5; padding: 0 1; }
    SlotLargeCard #chart-section { height: 1fr; padding: 0 1; min-height: 8; }
    SlotLargeCard #logs-toolbar { height: 3; padding: 0 1; }
    SlotLargeCard #log-view { height: 1fr; border: solid $primary; }
    """

    # ── Кэш виджетов (аннотации на уровне класса) ─────────
    _v_lbl: Static
    _a_lbl: Static
    _wt_lbl: Static
    _step_lbl: Static
    _header: Static
    _elapsed: Static
    _progress: ProgressBar
    _f_bios: Static
    _f_os: Static
    _f_shell: Static
    _f_ws: Static
    _f_serial: Static
    _chart: Sparkline

    _power_btn: Button

    # ── Реактивные атрибуты ───────────────────────────────
    voltage = reactive(0.0)
    current = reactive(0.0)
    power = reactive(0.0)
    step = reactive(0)
    state = reactive("IDLE")
    bios = reactive(False)
    os = reactive(False)
    shell = reactive(False)
    ws_alive = reactive(False)
    serial_alive = reactive(False)

    power_is_on = reactive(False, init=False)

    tests = [
        "all", "usb-benchmark", "lan-benchmark",
        "serial-benchmark", "dp-test", "stress-test",
    ]

    def __init__(self, slot: Slot, **kwargs):
        super().__init__(**kwargs)
        self.slot = slot
        self._paused = False
        self._v_history: list[float] = [0.0] * 40

    @property
    def logger(self):
        return logging.getLogger(f"slot_{self.slot.slot_id}")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(
                ListItem(Label("MAIN"), id="li-main", classes="tab-item"),
                ListItem(Label("TESTS"), id="li-tests", classes="tab-item"),
                ListItem(Label("DASHBOARD"), id="li-dashboard", classes="tab-item"),
                ListItem(Label("INFO"), id="li-info", classes="tab-item"),
                ListItem(Label("LOGS"), id="li-logs", classes="tab-item"),
                id="sidebar",
                classes="left-panel",
            )
            with Vertical(id="right"):
                # --- MAIN ---

                with Vertical(id="panel-main", classes="panel"):
                    yield Label(
                        f"SLOT: {self.slot.name} | s/n: {self.slot.board.board_id} | state: {self.slot.state.name}",
                        id="dash-header",
                        classes="title")
                    with Horizontal():
                        yield Label("BIOS", id="f-bios", classes="list-item")
                        yield Label("OS", id="f-os", classes="list-item")
                        yield Label("SHELL", id="f-shell", classes="list-item")
                        yield Label("WS", id="f-ws", classes="list-item")
                        yield Label("SERIAL", id="f-serial", classes="list-item")
                    with Vertical(classes="container main"):
                        with Horizontal(classes=""):
                            yield Label("Current test:", classes="list-item")
                            yield Static("Value", classes="list-item left")
                        with Horizontal():
                            yield Label("Current step:", classes="list-item")
                            yield Static("Value", id="m-step", classes="list-item left")
                        with Horizontal():
                            yield Label("Step time:", classes="list-item")
                            yield Static("00:00:00", classes="list-item left")
                        with Horizontal():
                            yield Label("Test time:", classes="list-item")
                            yield Static("00:00:00", classes="list-item left")

                    with Horizontal(classes="btn-panel"):
                        yield Button("RESET", classes="btn", variant="warning")
                        yield Button("REBOOT", classes="btn", variant="primary")
                        yield Button("POWER", id="btn-power-single", classes="btn")

                # --- TESTS ---
                with Vertical(id="panel-tests", classes="panel"):
                    yield Label(f"SLOT: {self.slot.name} | s/n: {self.slot.board.board_id}", classes="title")

                    yield SelectionList[tuple[str, str]](
                        *[(test, test, False) for test in self.tests],
                        id="tests-list", classes="list",
                    )
                    with Horizontal(classes="main"):
                        with Vertical():
                            yield Static("Test logs:", classes="muted")
                            yield RichLog(
                                id=f"test-log-slot-{self.slot.slot_id}",
                                classes="slot-log-view",
                                highlight=True, markup=True, wrap=True,
                                auto_scroll=True, max_lines=1000,
                            )
                        with Vertical():
                            yield Static("Step logs:", classes="muted")
                            yield RichLog(
                                id=f"step-log-slot-{self.slot.slot_id}",
                                classes="slot-log-view",
                                highlight=True, markup=True, wrap=True,
                                auto_scroll=True, max_lines=1000,
                            )

                    with Vertical(id="progress-section"):
                        yield Static("Test progress:", classes="muted")
                        yield ProgressBar(total=10, show_eta=False, id="dash-progress")
                        yield Static("Elapsed: 00:00:00", id="dash-elapsed")

                    with Horizontal(classes="btn-panel"):
                        yield Button("START", classes="btn", variant="success")
                        yield Button("STOP", classes="btn", variant="error")

                # --- DASHBOARD ---
                with Vertical(id="panel-dashboard", classes="panel container"):
                    yield Static(
                        f"  DASHBOARD  |  {self.slot.name}  |  state: {self.slot.state.name}",
                        id="dash-header", classes="title",
                    )
                    yield Grid(
                        Static("VOLTAGE", classes="metric"),
                        Static("0.0 V", id="m-v", classes="metric metric-value"),
                        Sparkline(self._v_history, id="v-chart", summary_function=max, classes="metric"),
                        Static("CURRENT", classes="metric"),
                        Static("0.0 A", id="m-a", classes="metric metric-value"),
                        Sparkline(self._v_history, id="c-chart", summary_function=max, classes="metric"),
                        Static("POWER", classes="metric"),
                        Static("0.0 Wt", id="m-wt", classes="metric metric-value"),
                        Sparkline(self._v_history, id="wt-chart", summary_function=max, classes="metric"),
                        id="metrics-grid",
                    )
                # --- INFO ---
                with Vertical(id="panel-info", classes="panel"):
                    yield Placeholder("I'm an INFO")

                # --- LOGS ---
                with Vertical(id="panel-logs", classes="panel"):
                    with Horizontal(id="logs-toolbar"):
                        yield Button("Clear", id="log-clear", variant="default", compact=True)
                        yield Button("Pause", id="log-pause", variant="warning", compact=True)
                        yield Static(f"Slot: {self.slot.name}", classes="muted")
                    yield RichLog(
                        id=f"log-slot-{self.slot.slot_id}",
                        classes="slot-log-view",
                        highlight=True, markup=True, wrap=True,
                        auto_scroll=True, max_lines=1000,
                    )

    # ── Асинхронные колбэки ─────────────────────────────
    async def _on_state(self, slot_id: str, old: str, new: str) -> None:
        self.state = new

    def _on_power_change(self, is_on: bool) -> None:
        self.power_is_on = is_on

    async def _on_metrics(self, slot_id: str, v: float, a: float, wt: float) -> None:
        self.voltage = v
        self.current = a
        self.power = wt

    async def _on_board(self, slot_id: str, bios: bool, os: bool, shell: bool) -> None:
        self.bios = bios
        self.os = os
        self.shell = shell

    async def _on_progress(self, slot_id: str, step: int, test_time: timedelta) -> None:
        self.step = step
        self._elapsed.update(f"Elapsed: {test_time}")

    async def _on_channels(self, slot_id: str, ws: bool, serial: bool, log: bool) -> None:
        self.ws_alive = ws
        self.serial_alive = serial

    # ── Watch-методы ────────────────────────────────────
    def watch_voltage(self, v: float) -> None:
        self._v_lbl.update(f"{v:.2f} V")
        self.push_voltage_sample(v)

    def watch_current(self, a: float) -> None:
        self._a_lbl.update(f"{a:.2f} A")

    def watch_power(self, wt: float) -> None:
        self._wt_lbl.update(f"{wt:.2f} Wt")

    def watch_step(self, step: int) -> None:
        self._step_lbl.update(str(step))
        self._progress.update(total=10, progress=min(step, 10))

    def watch_state(self, state: str) -> None:
        self._header.update(
            f"  DASHBOARD  |  {self.slot.name}  |  state: {state}"
        )

    def watch_bios(self, v: bool) -> None:
        self._watch_flag(self._f_bios, "BIOS", v)

    def watch_os(self, v: bool) -> None:
        self._watch_flag(self._f_os, "OS", v)

    def watch_shell(self, v: bool) -> None:
        self._watch_flag(self._f_shell, "SHELL", v)

    def watch_ws_alive(self, v: bool) -> None:
        self._watch_flag(self._f_ws, "WS", v)

    def watch_serial_alive(self, v: bool) -> None:
        self._watch_flag(self._f_serial, "SERIAL", v)

    def watch_power_is_on(self, is_on: bool) -> None:
        try:
            btn = self.query_one("#btn-power-single", Button)
            if is_on:
                btn.label = "POWER OFF"
                btn.variant = "error"
            else:
                btn.label = "POWER ON"
                btn.variant = "success"
        except Exception:
            pass

    def _watch_flag(self, widget: Static, label: str, value: bool) -> None:
        widget.update(label)
        widget.remove_class("enabled", "disabled")
        widget.add_class("enabled" if value else "disabled")

    def push_voltage_sample(self, v: float) -> None:
        self._v_history.append(v)
        if len(self._v_history) > 40:
            self._v_history.pop(0)
        self._chart.data = self._v_history

    # ── Fallback-обновление ─────────────────────────────
    def update_dashboard(self) -> None:
        self.voltage = self.slot.v
        self.current = self.slot.a
        self.power = self.slot.wt
        self.step = self.slot.step
        self.state = self.slot.state.name
        if self.slot.board:
            self.bios = self.slot.board.bios
            self.os = self.slot.board.os
            self.shell = self.slot.board.shell
        self.ws_alive = self.slot.ws_alive
        self.serial_alive = self.slot.serial_alive
        self._elapsed.update(f"Elapsed: {self.slot.test_time}")

    # ── Логирование ─────────────────────────────────────
    def append_log(self, message: str, level: str = "INFO") -> None:
        if self._paused:
            return
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(message)

    # ── Обработчики кнопок ──────────────────────────────
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "log-clear":
            self.query_one(f"#log-slot-{self.slot.slot_id}", RichLog).clear()
            self.logger.info("Button 'clear log' was pressed")
        elif button_id == "log-pause":
            self._paused = not self._paused
            event.button.label = "Resume" if self._paused else "Pause"
        elif button_id == "btn-power-single":
            self.append_log("Power toggle", "INFO")
            self.logger.info("Button 'single power' was pressed")
            if self.slot.state in (SlotState.IDLE, SlotState.OFFLINE, SlotState.ERROR):
                self.run_worker(self._power_on(), exclusive=True)
            else:
                self.run_worker(self._power_off(), exclusive=True)
        elif button_id == "btn-start":
            self.append_log("Test start", "INFO")
            self.run_worker(self._start_tests(), exclusive=True)
        elif button_id == "btn-stop":
            self.append_log("Stop", "WARNING")
            self.run_worker(self._power_off(), exclusive=True)
        elif button_id == "btn-reboot":
            self.append_log("Reboot", "INFO")
            self.run_worker(self._reboot(), exclusive=True)
        elif button_id == "btn-reset":
            self.append_log("Reset board", "INFO")
            if self.slot.board:
                self.slot.board.reset()
            self.update_dashboard()

    async def _power_on(self):
        try:
            if self.slot.state in (SlotState.IDLE, SlotState.OFFLINE):
                await self.slot.start()
            else:
                await self.slot.stop()
        except Exception as e:
            self.append_log(f"Power on failed: {e}", "ERROR")

    async def _power_off(self):
        try:
            await self.slot.stop()
        except Exception as e:
            self.append_log(f"Power off failed: {e}", "ERROR")

    async def _start_tests(self):
        if not self.slot.is_ready:
            self.append_log("Slot not ready. Power on first.", "WARNING")
            return
        try:
            tests_list = self.query_one("#tests-list", SelectionList)
            selected_tests = list(tests_list.selected)
        except Exception:
            selected_tests = self.tests[:1]
        for test in selected_tests:
            asyncio.create_task(self.slot.execute(test, progress_timeout=30.0))

    async def _reboot(self):
        try:
            await self.slot.power_control.power_off()
            await asyncio.sleep(1)
            await self.slot.power_control.power_on()
        except Exception as e:
            self.append_log(f"Reboot failed: {e}", "ERROR")

    # ── Жизненный цикл ──────────────────────────────────
    def on_mount(self) -> None:
        # Кэшируем виджеты (один раз)
        self._v_lbl = self.query_one("#m-v", Static)
        self._a_lbl = self.query_one("#m-a", Static)
        self._wt_lbl = self.query_one("#m-wt", Static)
        self._step_lbl = self.query_one("#m-step", Static)
        self._header = self.query_one("#dash-header", Static)
        self._elapsed = self.query_one("#dash-elapsed", Static)
        self._progress = self.query_one("#dash-progress", ProgressBar)
        self._f_bios = self.query_one("#f-bios", Static)
        self._f_os = self.query_one("#f-os", Static)
        self._f_shell = self.query_one("#f-shell", Static)
        self._f_ws = self.query_one("#f-ws", Static)
        self._f_serial = self.query_one("#f-serial", Static)
        self._chart = self.query_one("#v-chart", Sparkline)

        self._power_btn = self.query_one("#btn-power-single", Button)

        # Подписываемся на события слота
        self.slot.on_state_change(self._on_state)
        self.slot.on_metrics_change(self._on_metrics)
        self.slot.on_board_change(self._on_board)
        self.slot.on_progress_change(self._on_progress)
        self.slot.on_channels_change(self._on_channels)

        # Инициализация реактивных атрибутов
        self.voltage = self.slot.v
        self.current = self.slot.a
        self.power = self.slot.wt
        self.step = self.slot.step
        self.state = self.slot.state.name
        if self.slot.board:
            self.bios = self.slot.board.bios
            self.os = self.slot.board.os
            self.shell = self.slot.board.shell
        self.ws_alive = self.slot.ws_alive
        self.serial_alive = self.slot.serial_alive
        self._elapsed.update(f"Elapsed: {self.slot.test_time}")

        self.query_one("#panel-main").add_class("visible")
        self.query_one("#sidebar", ListView).index = 0

        self.slot.on_power_state_change(self._on_power_change)
        self.watch_power_is_on(self.power_is_on)

        self.append_log(f"Card for {self.slot.name} mounted", "INFO")



    def on_list_view_selected(self, event: ListView.Selected) -> None:
        for panel in self.query(".panel"):
            panel.remove_class("visible")
        panel_id = event.item.id.replace("li-", "panel-")
        self.query_one(f"#{panel_id}").add_class("visible")
        if panel_id == "panel-logs":
            self.query_one(f"#log-slot-{self.slot.slot_id}", RichLog).focus()
        elif panel_id == "panel-dashboard":
            self.update_dashboard()