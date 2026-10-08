import asyncio
import signal
from app.config import load_config
from app.registry import Registry
from app.session_manager import SessionManager


async def main():
    cfg = load_config("slots.yaml")
    reg = Registry(cfg)
    mgr = SessionManager(reg)

    mgr.on_state_change(
        lambda pc_id, state: print(f">>> state {pc_id} = {state}")
    )

    stop_event = asyncio.Event()

    # Обработчик Ctrl+C внутри цикла событий
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await mgr.start_all()
    try:
        await stop_event.wait()
    finally:
        print("\nОстанавливаю сессии...")
        await mgr.stop_all()
        print("Готово.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем")