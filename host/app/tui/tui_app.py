from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Grid
from textual.content import Content
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Label, Button, TabbedContent, TabPane, Header,
)

from host.app.slots.slot import Slot, SlotState
from host.app.slots.slot_fabric import SlotFabric
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
        yield TopPanel()
        with Grid(classes="slot-grid"):
            for slot in self.slots:
                yield MiniSlotCard(slot, classes="cell")


class TopPanel(Widget):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="container"):
            with Horizontal(id="top-panel-statistic", classes="container align-left"):
                yield Label("Statistic: ---")
                yield Label("Active slots: ---", id="top-panel-active-slots")
                yield Label("Errors: ---")
            with Horizontal(id="top-panel-btns", classes="container align-right"):
                yield Button("POWER ON", id="btn-power-all", classes="button", compact=True, variant="success")
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

    active_slots = reactive(int)
    all_slots_powered = reactive(False, init=False)

    def __init__(self, registry: SlotFabric, manager: SlotManager):
        super().__init__()
        self.registry = registry
        self.manager = manager
        self.active_slots = len(registry.slots)
        self.tests = [
            "all", "usb-benchmark", "lan-benchmark",
            "serial-benchmark", "dp-test", "stress-test",
        ]

    @property
    def slots(self) -> dict[str, Slot]:
        return self.manager.all_slots()

    def compose(self) -> ComposeResult:
        yield Header(classes="header", show_clock=True)
        yield MainTabWidget(list(self.slots.values()), classes="big")

    def on_mount(self) -> None:
        self.title = "Automation testing stand"
        active_slots_label = self.query_one("#top-panel-active-slots", Label)
        active_slots_label.update(f"Active slots: {self.active_slots}")

        # Подписываемся на события от менеджера
        self.manager.on_state_change(self._on_state_change)

        # Подписываемся на питание каждого слота
        for slot in self.slots.values():
            slot.on_power_state_change(self._on_any_slot_power_change)

        # Первичный пересчёт
        self._recalc_all_powered()

        # Запускаем бэкенд в воркере
        self.run_worker(self._start_backend(), exclusive=True)


    async def _start_backend(self):
        try:
            await self.manager.start_router()
        except Exception as e:
            self.notify(f"Backend start failed: {e}", severity="error")

    def _on_state_change(self, slot_id: str, old_state: str, new_state: str):
        """Вызывается при смене состояния слота."""
        pass

    def _on_any_slot_power_change(self, is_on: bool) -> None:
        """Любое изменение питания любого слота → пересчёт агрегата."""
        self._recalc_all_powered()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-btn":
            await self.push_screen(
                ChooseSlotsStartModal(list(self.slots.values()), self.tests),
                self.handle_slots_chosen,
            )
        elif event.button.id == "stop-btn":
            self.run_worker(self._stop_all(), exclusive=True)
        elif event.button.id == "btn-power-all":
            if self.all_slots_powered:
                self.run_worker(self._stop_all(), exclusive=True)
            else:
                self.run_worker(self._start_all(), exclusive=True)

    async def _start_all(self) -> None:
        try:
            await self.manager.start_all()
        except Exception as e:
            self.notify(f"Backend start failed: {e}", severity="error")

    async def _stop_all(self):
        try:
            await self.manager.stop_all()
        except Exception as e:
            self.notify(f"Stop failed: {e}", severity="error")

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

    def _recalc_all_powered(self) -> None:
        """Пересчитать, все ли слоты под питанием."""
        self.all_slots_powered = all(
            slot.power_control.is_on for slot in self.slots.values()
        )

    def watch_all_slots_powered(self, all_on: bool) -> None:
        """Единственная точка, где меняется внешний вид глобальной кнопки."""
        try:
            btn = self.query_one("#btn-power-all", Button)
            if all_on:
                btn.label = "POWER OFF ALL"
                btn.variant = "error"
            else:
                btn.label = "POWER ON ALL"
                btn.variant = "success"
        except Exception:
            pass

    def action_show_slot_info(self, slot_name: str) -> None:
        self.notify(f"Slot info: {slot_name}")

    async def on_unmount(self) -> None:
        if self.manager:
            await self.manager.stop_all()
