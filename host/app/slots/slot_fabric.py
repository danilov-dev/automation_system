"""
Registry — конструктор объектов на основе AppConfig.
Собирает на каждый слот:
WsChannel + SerialChannel + LogWatcher + BootAutomation + PowerControl -> Slot
Board -> Slot.board
И один общий WsRouter, в который регистрируются все WsChannel.
НЕ запускает ничего. Старт/стоп — забота SlotManager.
"""
import logging

from host.app.hardware.transports import SerialRtsTransport
from host.app.board import Board
from host.app.routers.ws_router import WsRouter
from host.app.channels.ws_channel import WsChannel
from host.app.channels.serial_channel import SerialChannel
from host.app.config import AppConfig, SlotConfig
from host.app.log_watcher import LogWatcher
from host.app.boot_automation import BootAutomation
from .slot import Slot
from host.app.hardware.power_control import PowerControl
from host.app.testing.storage import ResultStorage, JsonlResultStorage

logger = logging.getLogger(__name__)


class SlotFabric:
    def __init__(self, cfg: AppConfig, storage: ResultStorage | None = None):
        self.cfg = cfg
        self.router = WsRouter(
            host=cfg.ws.host,
            port=cfg.ws.port,
            hello_timeout=cfg.ws.hello_timeout,
            ping_interval=cfg.ws.ping_interval,
            ping_timeout=cfg.ws.ping_timeout,
        )
        self.slots: dict[str, Slot] = {}
        self._storage = storage or JsonlResultStorage()

        for slot_cfg in cfg.slots.values():
            self._build_slot(slot_cfg)
        logger.info(f"[SlotFabric] собрано слотов: {len(self.slots)}")

    def _build_slot(self, sc: SlotConfig) -> None:
        # Создаём каналы
        ws = WsChannel(sc.slot_id)
        serial = SerialChannel(sc.slot_id, sc.com_ctrl)

        # Регистрируем WebSocket-канал в роутере
        self.router.register(ws)

        # Создаём остальные компоненты
        watcher = LogWatcher(sc.slot_id, sc.com_log)
        boot = BootAutomation(watcher, sc.login, sc.password)
        power_transport = SerialRtsTransport(sc.com_power)
        power_control = PowerControl(sc.slot_id, power_transport)

        # Создаём плату
        board = Board(board_id=sc.slot_id)

        # Создаём Slot с инжектированным хранилищем
        slot = Slot(
            slot_id=sc.slot_id,
            ws_channel=ws,
            serial_channel=serial,
            log_watcher=watcher,
            boot_automation=boot,
            power_control=power_control,
            storage=self._storage,
        )
        slot.board = board
        self.slots[sc.slot_id] = slot

    def get(self, slot_id: str) -> Slot | None:
        return self.slots.get(slot_id)

    def all_slot_ids(self) -> list[str]:
        return list(self.slots.keys())