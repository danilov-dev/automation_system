import asyncio
import argparse
from pathlib import Path

from .app.config import load_config
from .app.registry import Registry
from .app.slot_manager import SlotManager
from .app.tui.tui_app import HMI

ROOT_DIR = Path(__file__).resolve().parents[2]

async def main():
    parser = argparse.ArgumentParser(description="Запуск стенда")
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Запустить приложение в режиме текстового интерфейса (TUI)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=f"./slots.yaml",
        help="Путь к файлу конфигурации slots.yaml"
    )
    args = parser.parse_args()

    config_file = Path(__file__).parent / args.config
    cfg = load_config(config_file)
    reg = Registry(cfg)
    mgr = SlotManager(reg)

    # 3. Разделяем логику запуска в зависимости от флага --tui
    if args.tui:
        app = HMI(config_path=str(config_file))
        await app.run_async()
    else:
        mgr.on_state_change(
            lambda pc_id, state: print(f">>> state {pc_id} = {state}")
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
