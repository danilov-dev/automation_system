"""
TUI для automation_system на базе Textual.
Использует SlotManager и Slot (PcSession поглощён в Slot).

Запуск:
    python tui.py [slots.yaml]
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Header, Footer, Static, Button, Label,
    Log, Rule
)
from textual.reactive import reactive
from textual import on, work

from app.config import load_config
from app.registry import Registry
from app.slot_manager import SlotManager
from app.slot import Slot, SlotState


# ═══════════════════════════════════════════════════════════════
#  CSS-стили
# ═══════════════════════════════════════════════════════════════
APP_CSS = """
Screen {
    layout: vertical;
}

#top-bar {
    height: 3;
    background: $primary;
    color: $text;
    padding: 0 1;
    align: left middle;
}

#top-bar Button {
    margin: 0 1;
}

#counters {
    margin-left: 2;
    text-style: bold;
}

#main-area {
    height: 1fr;
    layout: horizontal;
}

#slots-column {
    width: 40%;
    border: solid $accent;
    padding: 1;
    overflow-y: auto;
}

#log-column {
    width: 1fr;
    border: solid $accent;
    padding: 1;
    layout: vertical;
}

#log-column Log {
    height: 1fr;
}

.slot-card {
    border: solid $secondary;
    padding: 1;
    margin: 1 0;
    min-height: 12;
}

.slot-card.focused {
    border: solid $warning;
}

.slot-title {
    text-style: bold;
    color: $accent;
}

.status-line {
    margin: 0 0 0 1;
}

.status-line.ok { color: #00ff00; }
.status-line.fail { color: #ff4444; }
.status-line.warn { color: #ffaa00; }

.slot-actions {
    margin-top: 1;
    align: right middle;
}

.slot-actions Button {
    margin: 0 1;
    min-width: 10;
}

#status-bar {
    height: 1;
    background: $secondary;
    color: $text;
    padding: 0 1;
}

#section-title {
    text-style: bold;
    color: $accent;
    margin-bottom: 1;
}

#status-line {
    color: grey;
}

.log-title {
    text-style: bold;
    color: $accent;
    margin-bottom: 1;
}
"""


# ═══════════════════════════════════════════════════════════════
#  Карточка слота
# ═══════════════════════════════════════════════════════════════
class SlotCard(Static):
    """Интерактивная карточка одного слота."""

    def __init__(self, pc_id: str, mgr: SlotManager, reg: Registry, app_ref):
        super().__init__()
        self.pc_id = pc_id
        self.mgr = mgr
        self.reg = reg
        self.app_ref = app_ref

    def compose(self) -> ComposeResult:
        yield Label(f"🖥  {self.pc_id}", classes="slot-title")
        yield Rule()
        yield Static("TCP:   ● ---", id="tcp-status", classes="status-line")
        yield Static("COM2:  ● ---", id="serial-status", classes="status-line")
        yield Static("COM1:  ● ---", id="log-status", classes="status-line")
        yield Static("Auth:  ---", id="auth-status", classes="status-line")
        yield Static("State: IDLE", id="state-status", classes="status-line")
        with Horizontal(classes="slot-actions"):
            yield Button("Ping", id=f"ping-{self.pc_id}", variant="primary")
            yield Button("Reboot", id=f"reboot-{self.pc_id}", variant="warning")
            yield Button("Info", id=f"info-{self.pc_id}", variant="default")

    # ── Обработка кнопок ──────────────────────────────────────
    @on(Button.Pressed)
    def on_button(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == f"ping-{self.pc_id}":
            self.app_ref.run_worker(self._run_ping(), exclusive=True)
        elif btn_id == f"reboot-{self.pc_id}":
            self.app_ref.run_worker(self._run_reboot(), exclusive=True)
        elif btn_id == f"info-{self.pc_id}":
            self.app_ref.run_worker(self._run_info(), exclusive=True)

    async def _run_ping(self):
        try:
            self.query_one("#state-status").update("State: PING...")
            slot = self.mgr.get_slot(self.pc_id)
            if slot is None:
                raise KeyError(f"slot {self.pc_id!r} не найден")

            result = await slot.execute(
                "ping", {"target": "8.8.8.8", "count": 2}
            )
            status = result.get("status", "unknown")
            self.query_one("#state-status").update(f"State: PING [{status}]")
            self.app_ref.system_log(f"[{self.pc_id}] Ping result: {status}")
        except Exception as e:
            self.query_one("#state-status").update("State: ERROR")
            self.app_ref.system_log(f"[{self.pc_id}] Ping error: {e}")

    async def _run_reboot(self):
        try:
            self.query_one("#state-status").update("State: REBOOTING...")
            slot = self.mgr.get_slot(self.pc_id)
            if slot is None:
                raise KeyError(f"slot {self.pc_id!r} не найден")

            await slot.execute("reboot", {})
            self.app_ref.system_log(f"[{self.pc_id}] Reboot command sent")
        except Exception as e:
            self.query_one("#state-status").update("State: ERROR")
            self.app_ref.system_log(f"[{self.pc_id}] Reboot error: {e}")

    async def _run_info(self):
        slot = self.mgr.get_slot(self.pc_id)
        if slot is None:
            return

        info_lines = [
            f"PC ID: {slot.pc_id}",
            f"State: {slot.state.name}",
            f"TCP port: {self.reg.router.port}",
            f"COM1 (log): {slot.log_watcher.port}",
            f"COM2 (ctrl): {slot.serial_channel.port}",
            f"Logged in: {slot.boot_automation.is_logged_in}",
            f"Last log lines: {len(slot.log_watcher.lines)}",
        ]
        self.app_ref.system_log(f"[{self.pc_id}] INFO: " + " | ".join(info_lines))

    # ─ Обновление статусов (вызывается из главного цикла) ────
    def refresh_status(self):
        slot = self.mgr.get_slot(self.pc_id)
        if slot is None:
            return

        tcp_alive = slot.tcp_channel.is_alive
        ser_alive = slot.serial_channel.is_alive
        log_alive = slot.log_watcher.is_alive
        logged_in = slot.boot_automation.is_logged_in

        self._update_indicator("#tcp-status", "TCP", tcp_alive)
        self._update_indicator("#serial-status", "COM2", ser_alive)
        self._update_indicator("#log-status", "COM1", log_alive)

        auth_text = "✓ Logged in" if logged_in else "✗ Not logged"
        auth_widget = self.query_one("#auth-status")
        auth_widget.update(f"Auth:  {auth_text}")
        auth_widget.remove_class("ok", "fail")
        auth_widget.add_class("ok" if logged_in else "fail")

        # Обновляем состояние
        self.set_state(slot.state.name)

        # Последние строки COM1
        if slot.log_watcher.lines:
            last_line = slot.log_watcher.lines[-1]
            self.app_ref.update_slot_log(self.pc_id, last_line)

    def _update_indicator(self, widget_id: str, name: str, alive: bool):
        widget = self.query_one(widget_id)
        text = "● ONLINE" if alive else "● OFFLINE"
        widget.update(f"{name}: {text}")
        widget.remove_class("ok", "fail")
        widget.add_class("ok" if alive else "fail")

    def set_state(self, state: str):
        widget = self.query_one("#state-status")
        widget.update(f"State: {state.upper()}")
        widget.remove_class("ok", "warn", "fail")

        state_lower = state.lower()
        if state_lower in ("idle", "ready", "power_on"):
            widget.add_class("ok")
        elif state_lower in ("reboot", "shutdown", "testing"):
            widget.add_class("warn")
        elif state_lower in ("error", "offline"):
            widget.add_class("fail")


# ═══════════════════════════════════════════════════════════════
#  Главное приложение
# ═══════════════════════════════════════════════════════════════
class AutomationTUI(App):
    """Главное TUI-приложение для стенда COM Express."""

    CSS = APP_CSS
    MOUSE_ENABLED = False  # Отключаем мышь для стабильности

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("s", "toggle_start", "Start/Stop", priority=True),
        Binding("r", "refresh_view", "Refresh"),
        Binding("l", "toggle_log", "Toggle Log"),
    ]

    # Reactive-состояния для счетчиков
    total_slots = reactive(0)
    active_slots = reactive(0)
    reboot_slots = reactive(0)
    com_ports_count = reactive(0)
    lan_count = reactive(0)

    def __init__(self, config_path: str = "slots.yaml"):
        super().__init__()
        self.config_path = config_path
        self.cfg = None
        self.reg: Registry | None = None
        self.mgr: SlotManager | None = None
        self.slot_cards: dict[str, SlotCard] = {}
        self.start_time: datetime | None = None
        self._slot_last_log: dict[str, str] = {}

    # ── Композиция UI ─────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Верхняя панель управления
        with Horizontal(id="top-bar"):
            yield Button("▶ Start All", id="btn-start", variant="success")
            yield Button("■ Stop All", id="btn-stop", variant="error")
            yield Static(
                "Slots: 0 | Active: 0 | Reboot: 0",
                id="counters"
            )

        # Основная область
        with Horizontal(id="main-area"):
            # Левая колонка — слоты
            with Vertical(id="slots-column"):
                yield Label("SLOTS", id="section-title")
                yield Rule()
                # Карточки слотов монтируются динамически в on_mount

            # Правая колонка — логи
            with Vertical(id="log-column"):
                yield Label("📋 SYSTEM LOG", classes="log-title")
                yield Rule()
                yield Log(id="system-log", highlight=True)
                yield Rule()
                yield Label("📟 SLOT ACTIVITY", classes="log-title")
                yield Log(id="slot-log", highlight=True)

        # Статус-бар
        yield Static("Ready — press 's' to start", id="status-bar")

        yield Footer()

    # ── Инициализация ────────────────────────────────────────
    def on_mount(self) -> None:
        self.start_time = datetime.now()
        self.title = "COM Express Test Stand"
        self.sub_title = f"Config: {self.config_path}"

        try:
            self.cfg = load_config(self.config_path)
            self.reg = Registry(self.cfg)
            self.mgr = SlotManager(self.reg)

            # Подписка на события изменения состояния
            self.mgr.on_state_change(self._on_state_change_sync)

            # Создаем карточки слотов
            slots_column = self.query_one("#slots-column")
            for pc_id in self.reg.all_pc_ids():
                card = SlotCard(pc_id, self.mgr, self.reg, self)
                card.add_class("slot-card")
                self.slot_cards[pc_id] = card
                slots_column.mount(card)

            self.total_slots = len(self.slot_cards)
            self.system_log(f"System initialized. Slots: {self.total_slots}")
            self.system_log(f"   TCP server: {self.cfg.tcp.host}:{self.cfg.tcp.port}")

            # Запускаем периодическое обновление
            self.set_interval(1.0, self._refresh_tick)

        except Exception as e:
            self.system_log(f"Init error: {e}")
            self.query_one("#status-bar").update(f"ERROR: {e}")

    # ── Обработка кнопок верхней панели ───────────────────────
    @on(Button.Pressed)
    def on_top_button(self, event: Button.Pressed):
        if event.button.id == "btn-start":
            self.run_worker(self._start_all(), exclusive=True)
        elif event.button.id == "btn-stop":
            self.run_worker(self._stop_all(), exclusive=True)

    async def _start_all(self):
        try:
            await self.mgr.start_all()
            self.system_log("All slots started")
        except Exception as e:
            self.system_log(f"Start error: {e}")

    async def _stop_all(self):
        try:
            await self.mgr.stop_all()
            self.system_log("All slots stopped")
        except Exception as e:
            self.system_log(f"Stop error: {e}")

    # ── Callback изменения состояния слота ────────────────────
    def _on_state_change_sync(self, pc_id: str, state: str):
        """Sync-callback от SlotManager. Делегируем в UI-поток."""
        self.call_next(self._handle_state_change, pc_id, state)

    def _handle_state_change(self, pc_id: str, state: str):
        self.system_log(f"[{pc_id}] State → {state.upper()}")
        if pc_id in self.slot_cards:
            self.slot_cards[pc_id].set_state(state)

    # ── Периодическое обновление ──────────────────────────────
    def _refresh_tick(self):
        """Вызывается каждую секунду для обновления UI."""
        if not self.reg:
            return

        # Обновляем карточки
        for pc_id, card in self.slot_cards.items():
            card.refresh_status()

        # Счетчики
        active = sum(
            1 for slot in self.reg.slots.values() if slot.tcp_channel.is_alive
        )
        reboot = sum(
            1 for slot in self.reg.slots.values()
            if not slot.boot_automation.is_logged_in
        )
        com_count = sum(
            (1 if slot.serial_channel.is_alive else 0) +
            (1 if slot.log_watcher.is_alive else 0)
            for slot in self.reg.slots.values()
        )
        lan_count = sum(
            1 for slot in self.reg.slots.values() if slot.tcp_channel.is_alive
        )

        self.active_slots = active
        self.reboot_slots = reboot
        self.com_ports_count = com_count
        self.lan_count = lan_count

        # Статус-бар
        if self.mgr and self.mgr.is_started:
            uptime = datetime.now() - self.start_time
            uptime_str = str(uptime).split(".")[0]
            self.query_one("#status-bar").update(
                f"● Running | Uptime: {uptime_str} | "
                f"COM: {com_count} | LAN: {lan_count} | "
                f"Slots: {self.total_slots}"
            )
        else:
            self.query_one("#status-bar").update(
                "○ Stopped — press 's' to start"
            )

    # ── Watchers для реактивных счетчиков ─────────────────────
    def watch_active_slots(self, value: int):
        self._update_counters()

    def watch_reboot_slots(self, value: int):
        self._update_counters()

    def _update_counters(self):
        self.query_one("#counters").update(
            f"Slots: {self.total_slots} | "
            f"Active: {self.active_slots} | "
            f"Reboot: {self.reboot_slots}"
        )

    # ── Логирование ──────────────────────────────────────────
    def system_log(self, message: str):
        """Добавить запись в общий системный лог."""
        log_widget = self.query_one("#system-log")
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_widget.write_line(f"[{timestamp}] {message}")

    def update_slot_log(self, pc_id: str, line: str):
        """Обновить лог активности слота (только если строка новая)."""
        last = self._slot_last_log.get(pc_id)
        if last == line:
            return
        self._slot_last_log[pc_id] = line
        log_widget = self.query_one("#slot-log")
        timestamp = datetime.now().strftime("%H:%M:%S")
        # Обрезаем длинные строки
        short_line = line[:80] + "..." if len(line) > 80 else line
        log_widget.write_line(f"[{timestamp}] [{pc_id}] {short_line}")

    # ── Actions (горячие клавиши) ─────────────────────────────
    def action_toggle_start(self):
        if self.mgr and self.mgr.is_started:
            self.run_worker(self._stop_all(), exclusive=True)
        else:
            self.run_worker(self._start_all(), exclusive=True)

    def action_refresh_view(self):
        self.system_log("Manual refresh")
        self._refresh_tick()

    def action_toggle_log(self):
        log_col = self.query_one("#log-column")
        log_col.display = not log_col.display


# ═══════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════
def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "slots.yaml"
    if not Path(config_path).exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    app = AutomationTUI(config_path)
    app.run()


if __name__ == "__main__":
    main()