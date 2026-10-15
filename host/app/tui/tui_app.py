import sys
from pathlib import Path
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Header, Footer, TabbedContent, TabPane, Label, Button
from textual import on

from host.app.config import load_config
from host.app.registry import Registry
from host.app.slot_manager import SlotManager
from host.app.slot import SlotState

from .modules.slot import SlotCard, MiniSlotCard
from .modules.top_bar import TopBar

SLOT_NAMES = ["SLOT 1", "SLOT 2", "SLOT 3", "SLOT 4"]
SLOT_IDS = ["slot1", "slot2", "slot3", "slot4"]

ROOT_DIR = Path(__file__).resolve().parents[2]

class HMI(App):
    """Графическая оболочка для стенда"""
    CSS_PATH = "styles/main.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("s", "toggle_start", "Start/Stop", priority=True),
    ]

    def __init__(self, config_path: str = f"{ROOT_DIR}/slots.yaml"):
        super().__init__()
        self.config_path = config_path
        self.cfg = None
        self.reg: Registry | None = None
        self.mgr: SlotManager | None = None
        self.slot_cards: dict[str, SlotCard | MiniSlotCard] = {}
        self.configured_slots: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield TopBar()

        with TabbedContent(SLOT_NAMES[0], id="tabs"):
            with TabPane("ALL", id="tab-all"):
                yield Container(id="main-area-all", classes="mini-grid")

            for i, name in enumerate(SLOT_NAMES):
                with TabPane(name, id=f"tab-{i + 1}"):
                    yield Container(id=f"slot-area-{i + 1}")

        yield Footer(Label("Ready — press 's' to start"), id="bottom-bar")

    def on_mount(self) -> None:
        self.title = "COM Express Test Stand"
        self.sub_title = f"Config: {self.config_path}"

        try:
            self.cfg = load_config(self.config_path)
            self.reg = Registry(self.cfg)
            self.mgr = SlotManager(self.reg)
            self.mgr.on_state_change(self._on_state_change_sync)

            self.configured_slots = set(self.reg.all_pc_ids())

            tabs = self.query_one(TabbedContent)
            all_area = self.query_one("#main-area-all")

            for i, pc_id in enumerate(SLOT_IDS):
                is_configured = pc_id in self.configured_slots

                # 1. Мини-карточка
                mini_card = MiniSlotCard(pc_id=pc_id, classes="slot-card")
                if is_configured:
                    mini_card.add_class("configured")
                mini_card.mgr = self.mgr
                mini_card.app_ref = self
                self.slot_cards[f"mini_{pc_id}"] = mini_card
                all_area.mount(mini_card)

                # 2. Полная карточка
                full_card = SlotCard(pc_id=pc_id, classes="slot-card-large")
                if is_configured:
                    full_card.add_class("configured")
                    full_card.set_configured(True)  # ✅ Включаем кнопки
                else:
                    full_card.set_configured(False)  # ✅ Выключаем кнопки
                full_card.mgr = self.mgr
                full_card.app_ref = self
                self.slot_cards[f"full_{pc_id}"] = full_card

                slot_area = self.query_one(f"#slot-area-{i + 1}")
                slot_area.mount(full_card)

            self.set_interval(1.0, self._refresh_tick)
            self.notify(f"System initialized. Configured slots: {len(self.configured_slots)}")

        except Exception as e:
            self.notify(f"Init error: {e}", severity="error")

    @on(Button.Pressed)
    def on_top_button(self, event: Button.Pressed):
        if event.button.id == "btn-start":
            self.run_worker(self._start_all(), exclusive=True)
        elif event.button.id == "btn-stop":
            self.run_worker(self._stop_all(), exclusive=True)

    async def _start_all(self):
        try:
            await self.mgr.start_all()
            self.notify("All slots started", severity="information")
        except Exception as e:
            self.notify(f"Start error: {e}", severity="error")

    async def _stop_all(self):
        try:
            await self.mgr.stop_all()
            self.notify("All slots stopped", severity="warning")
        except Exception as e:
            self.notify(f"Stop error: {e}", severity="error")

    def action_toggle_start(self):
        if self.mgr and self.mgr.is_started:
            self.run_worker(self._stop_all(), exclusive=True)
        else:
            self.run_worker(self._start_all(), exclusive=True)

    def _on_state_change_sync(self, pc_id: str, state: str):
        try:
            self.call_next(self._handle_state_change, pc_id, state)
        except Exception:
            pass

    def _handle_state_change(self, pc_id: str, state: str):
        self.notify(f"[{pc_id}] State → {state.upper()}", severity="warning")
        self._refresh_slot(pc_id)

    def _refresh_tick(self):
        if not self.reg or self.mgr is None:
            return

        total = len(self.configured_slots)
        active = 0
        reboot = 0

        for pc_id in self.configured_slots:
            slot = self.reg.get(pc_id)
            if slot is None:
                continue

            status = slot.get_status_dict()
            self._apply_status_to_cards(pc_id, status)

            if slot.state in (SlotState.READY, SlotState.TESTING):
                active += 1
            if slot.state == SlotState.REBOOTING:
                reboot += 1

        try:
            self.query_one(TopBar).update_counters(total, active, reboot)
        except Exception:
            pass

    def _refresh_slot(self, pc_id: str):
        if pc_id not in self.configured_slots:
            return
        slot = self.mgr.get_slot(pc_id)
        if slot:
            self._apply_status_to_cards(pc_id, slot.get_status_dict())

    def _apply_status_to_cards(self, pc_id: str, status: dict):
        mini_card = self.slot_cards.get(f"mini_{pc_id}")
        if mini_card:
            mini_card.update_from_slot(status)

        full_card = self.slot_cards.get(f"full_{pc_id}")
        if full_card:
            full_card.update_from_slot(status)


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else f"{ROOT_DIR}/slots.yaml"
    HMI(config_path).run()