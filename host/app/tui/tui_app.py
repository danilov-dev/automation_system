from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical,Grid
from textual.content import Content
from textual.widget import Widget
from textual.widgets import (
    Label, Button, TabbedContent, TabPane, Header,
)

from host.app.slot import Slot, SlotState
from host.app.slot_fabric import SlotFabric
from host.app.slot_manager import SlotManager
from .modals.start_test_modal import ChooseSlotsStartModal
from .modules.large_slot_card import SlotLargeCard
from .modules.mini_slot_card import MiniSlotCard


class DetailsSlotTab(TabPane):
    def __init__(self, slot: Slot, **kwargs):
        super().__init__(slot.name, **kwargs)
        self.slot = slot

    def compose(self):
        with Vertical():
            yield SlotLargeCard(self.slot, classes="cell")


class AllSlotsTab(TabPane):
    def __init__(self, slots: list[Slot], title: Content | str = "ALL", **kwargs):
        super().__init__(title, **kwargs)
        self.slots = slots

    def compose(self) -> ComposeResult:
        with Grid(classes="slot-grid"):
            for slot in self.slots:
                yield MiniSlotCard(slot, classes="cell")

class TopPanel(Widget):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="container"):
            with Horizontal(id="top-panel-statistic", classes="container align-left"):
                yield Label("Statistic: ---")
                yield Label("Active slots: ---")
                yield Label("Errors: ---")
            with Horizontal(id="top-panel-btns", classes="container align-right"):
                yield Button("POWER", id="btn-power-all", classes="button active", compact=True)
                yield Button("START", id="start-btn", classes="button", compact=True)
                yield Button("STOP", id="stop-btn", classes="button", compact=True)

class MainTabWidget(Widget):
    def __init__(self, slots: list[Slot], **kwargs):
        super().__init__(**kwargs)
        self.slots = slots
        self.slots_names = [slot.name for slot in self.slots]

    def compose(self) -> ComposeResult:
        with TabbedContent(*self.slots_names, id="tabs"):
            yield AllSlotsTab(self.slots, id="tab-all", classes="tab-panel")
            for slot in self.slots:
                yield DetailsSlotTab(slot)

class TestingApp(App):
    CSS_PATH = "./style.tcss"

    def __init__(self, registry: SlotFabric, manager: SlotManager):
        super().__init__()
        self.registry = registry
        self.manager = manager
        self.tests = [
            "all", "usb-benchmark", "lan-benchmark",
            "serial-benchmark", "dp-test", "stress-test",
        ]

    @property
    def slots(self) -> dict[str, Slot]:
        return self.manager.all_slots()

    def compose(self) -> ComposeResult:
        yield Header(classes="header", show_clock=True)
        yield TopPanel()
        yield MainTabWidget(list(self.slots.values()), classes="big")

    def on_mount(self) -> None:
        self.title = "Automation testing stand"
        # Подписываемся на события от менеджера
        self.manager.on_state_change(self._on_state_change)
        # Периодическое обновление (как в старом рабочем TUI)
        self.set_interval(1.0, self._poll)
        # Запускаем бэкенд в воркере
        self.run_worker(self._start_backend(), exclusive=True)

    async def _start_backend(self):
        try:
            await self.manager.start_router()
        except Exception as e:
            self.notify(f"Backend start failed: {e}", severity="error")
        self._poll()

    def _poll(self):
        """Периодическое обновление всех карточек."""
        for card in self.query(MiniSlotCard):
            card.update_all()
        for card in self.query(SlotLargeCard):
            card.update_dashboard()
            card.push_voltage_sample(card.slot.v)

    def _on_state_change(self, slot_id: str, state: str):
        """Вызывается при смене состояния слота."""
        self.call_next(self._poll)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-btn":
            await self.push_screen(
                ChooseSlotsStartModal(list(self.slots.values()), self.tests),
                self.handle_slots_chosen,
            )
        elif event.button.id == "stop-btn":
            self.run_worker(self._stop_all(), exclusive=True)
        elif event.button.id == "btn-power-all":
            if self.manager.is_started:
                self.run_worker(self._stop_all(), exclusive=True)
            else:
                self.run_worker(self._start_all(), exclusive=True)

    async def _start_all(self) -> None:
        try:
            if not any(slot.state in (SlotState.IDLE, SlotState.READY, SlotState.OFFLINE) for slot in self.slots.values()):
                return
            await self.manager.start_all()
        except Exception as e:
            self.notify(f"Backend start failed: {e}", severity="error")

    async def _stop_all(self):
        try:
            await self.manager.stop_all()
        except Exception as e:
            self.notify(f"Stop failed: {e}", severity="error")
        self._poll()

    async def handle_slots_chosen(
        self, selected_slots: tuple[list[str], list[str]] | None
    ) -> None:
        if not selected_slots or not selected_slots[0] or not selected_slots[1]:
            self.notify("Launch canceled", severity="warning")
            return

        sel_slots, sel_tests = selected_slots
        self.notify(f"Slots starting: {', '.join(sel_slots)}")

        for slot_id in sel_slots:
            slot = self.slots.get(slot_id)
            if slot and slot.state in (SlotState.IDLE, SlotState.READY, SlotState.OFFLINE):
                try:
                    await self.manager.start_slot(slot_id)
                    for test in sel_tests:
                        asyncio.create_task(
                            slot.execute(test, progress_timeout=30.0)
                        )
                except Exception as e:
                    self.notify(f"Failed to start {slot_id}: {e}", severity="error")

        self._poll()

    def action_show_slot_info(self, slot_name: str) -> None:
        self.notify(f"Slot info: {slot_name}")

    async def on_unmount(self) -> None:
        if self.manager:
            await self.manager.stop_all()