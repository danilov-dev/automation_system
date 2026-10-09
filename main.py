import asyncio
from app.config import load_config
from app.registry import Registry
from app.slot_manager import SlotManager

async def main():
    cfg = load_config("slots.yaml")
    reg = Registry(cfg)
    mgr = SlotManager(reg)

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