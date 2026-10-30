from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Label, Button, SelectionList

from host.app.slots.slot import Slot


class ChooseSlotsStartModal(ModalScreen[tuple[list[str], list[str]]]):
    def __init__(self, slots: list[Slot], tests: list[str]) -> None:
        super().__init__()
        self.slots = slots
        self.tests = tests

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="container"):
            yield Label("Choose a slot for start:")
            with Horizontal(classes="container"):
                yield Button("Select all", id="select-all", variant="primary")
                yield Button("Clear selection", id="deselect-all", variant="default")
            yield SelectionList[tuple[str, str]](
                *[(slot.name, slot.name, False) for slot in self.slots],
                id="slots-list", classes="modal_selector",
            )
            yield SelectionList[tuple[str, str]](
                *[(test, test, False) for test in self.tests],
                id="tests-list", classes="modal_selector",
            )
            with Horizontal(classes="container"):
                yield Button("Start", id="start", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        sl = self.query_one("#slots-list", SelectionList)
        ts = self.query_one("#tests-list", SelectionList)
        if event.button.id == "select-all":
            sl.select_all()
        elif event.button.id == "deselect-all":
            sl.deselect_all()
        elif event.button.id == "start":
            self.dismiss((list(sl.selected), list(ts.selected)))
        elif event.button.id == "cancel":
            self.dismiss(())

    def on_mount(self) -> None:
        self.query_one("#slots-list", SelectionList).border_title = "Which slots?"
        self.query_one("#tests-list", SelectionList).border_title = "Which tests?"