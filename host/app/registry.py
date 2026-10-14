"""
Registry — конструктор объектов на основе AppConfig.

Собирает на каждый слот:
  TcpChannel + SerialChannel → Slot
  LogWatcher + BootAutomation

И один общий TcpRouter, в который регистрируются все TcpChannel.

НЕ запускает ничего. Старт/стоп — забота SlotManager.
"""
from host.app.channels.ws_channel import WsChannel
from .routers.ws_router import WsRouter
from .channels.serial_channel import SerialChannel
from .routers.tcp_router import TcpRouter
from .config import AppConfig, SlotConfig
from .log_watcher import LogWatcher
from .boot_automation import BootAutomation
from .slot import Slot
from .utils.console_logger import ConsoleLogger, LogLevel
from .hardware.power_control import PowerControl


class Registry:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

        self.router = WsRouter(
            host=cfg.ws.host,
            port=cfg.ws.port,
            hello_timeout=cfg.ws.hello_timeout,
            ping_interval= cfg.ws.ping_interval,
            ping_timeout=cfg.ws.ping_timeout,
        )

        self.slots: dict[str, Slot] = {}
        for slot_cfg in cfg.slots.values():
            self._build_slot(slot_cfg)

        ConsoleLogger.write(
            f"[Registry] собрано слотов: {len(self.slots)}",
            LogLevel.INFO,
        )

    def _build_slot(self, sc: SlotConfig) -> None:
        # Создаём каналы
        ws = WsChannel(sc.pc_id)
        serial = SerialChannel(sc.pc_id, sc.com_ctrl)

        # ВАЖНО: регистрируем Websocket-канал в роутере
        self.router.register(ws)

        # Создаём остальные компоненты
        watcher = LogWatcher(sc.pc_id, sc.com_log)
        boot = BootAutomation(watcher, sc.login, sc.password)
        power_control = PowerControl(sc.pc_id)

        # Создаём Slot — единую сущность (PcSession больше не нужен)
        slot = Slot(
            pc_id=sc.pc_id,
            ws_channel=ws,
            serial_channel=serial,
            log_watcher=watcher,
            boot_automation=boot,
            power_control=power_control,
        )

        self.slots[sc.pc_id] = slot

    def get(self, pc_id: str) -> Slot | None:
        return self.slots.get(pc_id)

    def all_pc_ids(self) -> list[str]:
        return list(self.slots.keys())