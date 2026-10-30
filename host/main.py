import asyncio
import argparse
import logging
from pathlib import Path
from host.app.config import load_config
from host.app.slots.slot_fabric import SlotFabric
from host.app.slot_manager import SlotManager
from host.app.logger.logger import setup_logging

ROOT_DIR = Path(__file__).resolve().parents[2]

LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


async def main():
    parser = argparse.ArgumentParser(description="Запуск стенда")
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Запустить приложение в режиме текстового интерфейса (TUI)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="./slots.yaml",
        help="Путь к файлу конфигурации slots.yaml",
    )
    parser.add_argument(
        "--log-config",
        type=str,
        default="./host/logging.yaml",
        help="Путь к файлу конфигурации логирования (по умолчанию ./logging.yaml)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=list(LOG_LEVELS.keys()),
        help="Уровень логирования (переопределяет значение из log-config). По умолчанию — как в файле.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Путь к файлу логов (переопределяет значение из log-config)",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Дополнительно выводить логи в stderr",
    )
    args = parser.parse_args()

    config_file = Path(__file__).parent / args.config
    log_level = LOG_LEVELS[args.log_level] if args.log_level else None

    cfg = load_config(config_file)
    reg = SlotFabric(cfg)
    mgr = SlotManager(reg)

    if args.tui:
        from host.app.tui.tui_app import TestingApp
        app = TestingApp(registry=reg, manager=mgr)
        setup_logging(
            app=app,
            log_config=args.log_config,
            log_file=args.log_file,
            level=log_level,
        )
        log = logging.getLogger(__name__)
        log.info("TUI started (config=%s)", args.log_config)
        await app.run_async()
    else:
        setup_logging(
            app=None,
            log_config=args.log_config,
            log_file=args.log_file,
            level=log_level,
            enable_console=args.console,
        )
        log = logging.getLogger(__name__)
        log.info("Console mode started (config=%s)", args.log_config)
        mgr.on_state_change(
            lambda slot_id, old_state, new_state: log.info(
                ">>> state %s: %s -> %s", slot_id, old_state, new_state
            )
        )
        await mgr.start_all()
        try:
            await asyncio.Event().wait()
        finally:
            await mgr.stop_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем")