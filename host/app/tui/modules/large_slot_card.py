import asyncio
import datetime
import logging

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Grid
from textual.widget import Widget
from textual.widgets import ListView, ListItem, Label, SelectionList, Button, Static, ProgressBar, Sparkline, RichLog

from .mini_slot_card import MiniSlotCard
from host.app.slot import Slot, SlotState


class SlotLargeCard(Widget):
    DEFAULT_CSS = """
    SlotLargeCard .panel { display: none; }
    SlotLargeCard .panel.visible { display: block; }
    SlotLargeCard #dashboard { height: 1fr; padding: 1; }
    SlotLargeCard #metrics-grid { height: 7; grid-size: 4 2; grid-gutter: 1; }
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

    tests = ["all", "usb-benchmark", "lan-benchmark",
             "serial-benchmark", "dp-test", "stress-test"]

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
                ListItem(Label("DASHBOARD"), id="li-dashboard", classes="tab-item"),
                ListItem(Label("LOGS"), id="li-logs", classes="tab-item"),
                id="sidebar",
                classes="left-panel",
            )
            with Vertical(id="right"):
                # --- MAIN ---
                with Horizontal(id="panel-main", classes="panel container"):
                    with Horizontal():
                        yield MiniSlotCard(self.slot)
                    with Vertical(classes="container"):
                        yield SelectionList[tuple[str, str]](
                            *[(test, test, False) for test in self.tests],
                            id="tests-list", classes="list",
                        )
                        with Grid(classes="container btn-grid"):
                            yield Button("POWER", id="btn-power-single", classes="button", compact=True)
                            yield Button("START", id="btn-start", classes="button", compact=True)
                            yield Button("STOP", id="btn-stop", classes="button", compact=True)
                            yield Button("REBOOT", id="btn-reboot", classes="button", compact=True)
                            yield Button("RESET", id="btn-reset", classes="button", compact=True)

                # --- DASHBOARD ---
                with Vertical(id="panel-dashboard", classes="panel"):
                    yield Static(
                        f"  DASHBOARD  |  {self.slot.name}  |  state: {self.slot.state.name}",
                        id="dash-header", classes="title",
                    )
                    yield Grid(
                        Static("VOLTAGE", classes="metric"),
                        Static("0.0 V", id="m-v", classes="metric metric-value"),
                        Static("CURRENT", classes="metric"),
                        Static("0.0 A", id="m-a", classes="metric metric-value"),
                        Static("POWER", classes="metric"),
                        Static("0.0 Wt", id="m-wt", classes="metric metric-value"),
                        Static("STEP", classes="metric"),
                        Static("0", id="m-step", classes="metric metric-value"),
                        id="metrics-grid",
                    )
                    with Horizontal(id="status-row"):
                        yield Static("Status: ", classes="muted")
                        yield Static("bios --", id="f-bios", classes="status-flag off")
                        yield Static("os --", id="f-os", classes="status-flag off")
                        yield Static("ws --", id="f-ws", classes="status-flag off")
                        yield Static("shell --", id="f-shell", classes="status-flag off")
                        yield Static("serial --", id="f-serial", classes="status-flag off")
                    with Vertical(id="progress-section"):
                        yield Static("Test progress:", classes="muted")
                        yield ProgressBar(total=10, show_eta=False, id="dash-progress")
                        yield Static("Elapsed: 00:00:00", id="dash-elapsed")
                    with Vertical(id="chart-section"):
                        yield Static("Voltage history (last 40 samples):", classes="muted")
                        yield Sparkline(self._v_history, id="v-chart", summary_function=max)

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

    # ---------- Dashboard ----------
    def update_dashboard(self) -> None:
        """Обновить все элементы dashboard из правильного места."""
        try:
            self.query_one("#m-v", Static).update(f"{self.slot.v:.2f} V")
            self.query_one("#m-a", Static).update(f"{self.slot.a:.2f} A")
            self.query_one("#m-wt", Static).update(f"{self.slot.wt:.2f} Wt")
            self.query_one("#m-step", Static).update(str(self.slot.step))

            # Флаги
            board = self.slot.board
            for flag in ("bios", "os", "ws", "shell", "serial_port"):
                widget_id = f"f-{flag}" if flag != "serial_port" else "f-serial"
                if flag == "ws":
                    value = self.slot.ws_alive
                elif flag == "serial_port":
                    value = self.slot.serial_alive
                else:
                    value = getattr(board, flag) if board else False

                w = self.query_one(f"#{widget_id}", Static)
                label = flag.replace("_port", "")
                w.update(f"{label} {'OK' if value else '--'}")
                w.remove_class("ok", "off")
                w.add_class("ok" if value else "off")

            # Прогресс
            total = 10
            progress = min(self.slot.step, total)
            bar = self.query_one("#dash-progress", ProgressBar)
            bar.update(total=total, progress=progress)

            # Время
            self.query_one("#dash-elapsed", Static).update(f"Elapsed: {self.slot.test_time}")

            # Заголовок
            self.query_one("#dash-header", Static).update(
                f"  DASHBOARD  |  {self.slot.name}  |  state: {self.slot.state.name}"
            )
        except Exception:
            pass

    def push_voltage_sample(self, v: float) -> None:
        """Добавить точку в график напряжения."""
        self._v_history.append(v)
        if len(self._v_history) > 40:
            self._v_history.pop(0)
        try:
            chart = self.query_one("#v-chart", Sparkline)
            chart.data = self._v_history
        except Exception:
            pass

    # ---------- Логирование ----------
    def append_log(self, message: str, level: str = "INFO") -> None:
        """Отправляем лог в стандартный логгер. Он сам уйдет в файл и в нужный RichLog."""
        if self._paused:
            return

        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(message)

    # ---------- Обработчики кнопок ----------
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
            self.logger.info(f"Button 'single power' was pressed")
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

    # ---------- Жизненный цикл ----------
    def on_mount(self) -> None:
        self.query_one("#panel-main").add_class("visible")
        self.query_one("#sidebar", ListView).index = 0
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