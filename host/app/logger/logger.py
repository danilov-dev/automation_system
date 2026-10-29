"""
Модуль настройки логирования.
Конфигурация берётся из YAML-файла (по умолчанию ./logging.yaml).
Поддерживает одновременный вывод:
  - в файл (с ротацией по дням)
  - в TUI (виджеты RichLog)
  - в консоль (опционально)
"""
import logging
import logging.config
from pathlib import Path
import yaml
from textual.app import App
from host.app.logger.log_handlers import SlotDispatcherHandler

DEFAULT_LOG_CONFIG = Path(__file__).resolve().parents[3] / "logging.yaml"


def _load_log_config(config_path: str | Path) -> dict:
    """Загружает словарь конфигурации логирования из YAML."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации логирования не найден: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(
    app: App | None = None,
    log_config: str | Path = DEFAULT_LOG_CONFIG,
    log_file: str | Path | None = None,
    level: int | str | None = None,
    enable_console: bool = False,
) -> logging.Logger:
    """
    Настраивает корневой логгер на основе YAML-конфига.

    :param app: Экземпляр Textual App. Если передан — логи будут выводиться в TUI.
    :param log_config: Путь к YAML-файлу конфигурации логирования.
    :param log_file: Переопределение пути к файлу логов (из CLI).
    :param level: Переопределение уровня логирования (из CLI).
    :param enable_console: Добавить ли консольный хэндлер (из CLI).
    :return: Настроенный корневой логгер.
    """
    cfg = _load_log_config(log_config)

    # --- Переопределения из CLI (имеют приоритет над файлом) ---
    if log_file is not None:
        cfg["handlers"]["file"]["filename"] = str(log_file)
        # Гарантируем, что директория существует
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    if level is not None:
        # level может быть int или строкой ("DEBUG", "INFO"...)
        if isinstance(level, int):
            level = logging.getLevelName(level)
        cfg["root"]["level"] = level

    if enable_console and "console" not in cfg["root"].get("handlers", []):
        cfg["root"].setdefault("handlers", []).append("console")

    # --- Применяем конфигурацию ---
    logger = logging.getLogger()
    if logger.handlers:
        logger.handlers.clear()

    logging.config.dictConfig(cfg)

    # --- TUI-хэндлер добавляем вручную (т.к. ему нужен экземпляр app) ---
    if app is not None:
        tui_handler = SlotDispatcherHandler(app)
        tui_fmt = cfg.get("formatters", {}).get("tui_fmt", {})
        tui_handler.setFormatter(
            logging.Formatter(
                fmt=tui_fmt.get("format", "%(asctime)s.%(msecs)03d %(message)s"),
                datefmt=tui_fmt.get("datefmt", "%H:%M:%S"),
            )
        )
        logger.addHandler(tui_handler)

    return logger