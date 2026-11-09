"""
Низкоуровневый WebSocket-клиент.
Отвечает ТОЛЬКО за соединение, переподключение и доставку JSON-сообщений.
Бизнес-логика вынесена в ClientManager.

Heartbeat не используется: WS-соединение само по себе сигнализирует о разрыве
(close frame / TCP RST), а сервер шлёт ping-фреймы на уровне протокола.
"""
import logging
import os
import json
import asyncio
from typing import Dict, Set, Any

import websockets
from websockets.asyncio.client import connect, ClientConnection
from dotenv import load_dotenv

from app.connection.events import ClientEvent, ClientEventHandler

load_dotenv()

logger = logging.getLogger(__name__)


class WebSocketClient:
    def __init__(
            self,
            url: str = "ws://127.0.0.1:8765",
            slot_id: str = "slot1",
    ):
        self.url = url
        self.slot_id = slot_id
        self._ws: ClientConnection | None = None
        self._connected = False

        self._connection_task: asyncio.Task | None = None
        self._reconnect_delay = float(os.getenv("RECONNECT_DELAY", 3.0))

        self._subscribers: Dict[ClientEvent, Set[ClientEventHandler]] = {
            e: set() for e in ClientEvent
        }

    # ── подписка ──────────────────────────────────────────
    def subscribe(self, event: ClientEvent, callback: ClientEventHandler):
        self._subscribers[event].add(callback)

    async def _notify(self, event: ClientEvent, data: Any = None):
        coros = [cb(event, data) for cb in self._subscribers[event]]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

    # ── lifecycle ─────────────────────────────────────────
    async def start(self):
        self._connection_task = asyncio.create_task(self._connection_loop())
        logger.info(f"WS Client запущен. Цель: {self.url}")

    async def stop(self):
        logger.info(f"Остановка WS клиента...")
        self._connected = False

        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info(f"WS Client остановлен")

    # ── основной цикл с переподключением ──────────────────
    async def _connection_loop(self):
        while True:
            try:
                async with connect(self.url) as ws:
                    self._ws = ws
                    self._connected = True
                    logger.info(f"[WS] Подключено к {self.url}")

                    # handshake
                    await self.send_json({"type": "hello", "slot_id": self.slot_id})
                    await self._notify(ClientEvent.CONNECTED)

                    # чтение до разрыва
                    await self._read_loop()

            except asyncio.CancelledError:
                break
            except (ConnectionRefusedError, OSError) as e:
                logger.error(f"[WS] {self.url} недоступен: {e}."
                    f"Повтор через {self._reconnect_delay}с")
            except websockets.ConnectionClosed as e:
                logger.info(f"[WS] Соединение закрыто: {e}")
            except Exception as e:
                logger.info(f"[[WS] Ошибка: {e}. Повтор через {self._reconnect_delay}с")
            finally:
                self._connected = False
                self._ws = None
                await self._notify(ClientEvent.DISCONNECTED)

            await asyncio.sleep(self._reconnect_delay)

    async def _read_loop(self):
        """Читает сообщения из ws и диспатчит их подписчикам."""
        if self._ws is None:
            return
        async for raw in self._ws:
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": raw}
            await self._notify(ClientEvent.MESSAGE_RECEIVED, parsed)

    async def send_json(self, data: dict):
        """Отправляет JSON-сообщение на сервер."""
        if self._ws is None or not self._connected:
            logger.info(f"[WS] Не подключено, сообщение не отправлено")
            return
        try:
            await self._ws.send(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.info(f"[WS] Ошибка отправки: {e}")
