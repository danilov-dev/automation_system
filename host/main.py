import asyncio
import argparse
from pathlib import Path

from host.app.config import load_config
from host.app.slot_fabric import SlotFabric
from host.app.slot_manager import SlotManager

ROOT_DIR = Path(__file__).resolve().parents[2]


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
    args = parser.parse_args()

    config_file = Path(__file__).parent / args.config

    # Создаём реестр и менеджер один раз
    cfg = load_config(config_file)
    reg = SlotFabric(cfg)
    mgr = SlotManager(reg)

    if args.tui:
        from host.app.tui.tui_app import TestingApp
        # Передаём уже созданные объекты в TUI
        app = TestingApp(registry=reg, manager=mgr)
        await app.run_async()
    else:
        mgr.on_state_change(
            lambda slot_id, state: print(f">>> state {slot_id} = {state}")
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