import logging
from textual.app import App
from textual.widgets import RichLog
from rich.text import Text

logger = logging.getLogger(__name__)

class SlotDispatcherHandler(logging.Handler):
    def __init__(self, app):
        super().__init__()
        self.app = app

    def emit(self, record):
        if not record.name.startswith("slot_"):
            return
        slot_id = record.name.split("_", 1)[1]
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return

        def _write():
            try:
                self._write_to_widget(slot_id, msg, record.levelname)
            except Exception as e:
                # хотя бы в файл, раз print не виден
                logging.getLogger("slot_dispatcher").exception(
                    "TUI log write failed for slot %s: %s", slot_id, e
                )

        try:
            self.app.call_later(_write)
        except Exception:
            self.handleError(record)

    def _write_to_widget(self, slot_id: str, msg: str, level: str):
        try:
            widget_id = f"log-slot-{slot_id}"
            log_widget = self.app.query_one(f"#{widget_id}", RichLog)
            colors = {
                "DEBUG": "dim", "INFO": "blue", "WARNING": "yellow",
                "ERROR": "bold red", "CRITICAL": "bold red"
            }
            color = colors.get(level, "white")
            # Разделяем время (первое слово) и само сообщение
            parts = msg.split(maxsplit=1)
            timestamp = parts[0] if parts else ""
            message = parts[1] if len(parts) > 1 else ""

            # ВАЖНО: Используем Text вместо строки с markup.
            # Это спасает от падения, когда в логе встречаются скобки вроде "[1]" или "[task_id]".
            text = Text()
            text.append(f"{timestamp} ", style="dim")
            text.append(f"{level:<7} ", style=color)
            text.append(message)  # Скобки здесь теперь просто текст
            log_widget.write(text)
        except Exception as e:
            # Временно выводим в консоль, если виджет не найден, чтобы видеть ошибки
            print(f"[TUI LOG ERROR] Slot {slot_id} not found or write failed: {e}")