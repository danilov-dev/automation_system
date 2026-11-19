import asyncio
import logging
from app.connection.ws_client import WebSocketClient
from app.connection.manager import Manager
from app.utils import ConsoleLogger, LogLevel

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

async def main():
    client = WebSocketClient(url="ws://10.30.0.199:8765", slot_id="slot1")
    manager = Manager(client)

    await client.start()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        ConsoleLogger.write("Stopping by Ctrl+C", LogLevel.INFO)
    finally:
        await client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nService stopped.")