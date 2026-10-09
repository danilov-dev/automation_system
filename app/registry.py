# app/registry.py

from dataclasses import dataclass

from app.channels.serial_channel import SerialChannel
from app.channels.tcp import TcpChannel
from app.channels.tcp_router import TcpRouter
from app.config import AppConfig, SlotConfig
from app.log_watcher import LogWatcher
from app.boot_automation import BootAutomation
from app.pc_session import PcSession
from app.slot import Slot
from app.utils.console_logger import ConsoleLogger, LogLevel


class Registry:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

        # Создаём TcpRouter ОДИН раз для всех слотов
        self.router = TcpRouter(
            host=cfg.tcp.host,
            port=cfg.tcp.port,
            hello_timeout=cfg.tcp.hello_timeout,
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
        tcp = TcpChannel(sc.pc_id)
        serial = SerialChannel(sc.pc_id, sc.com_ctrl)

        # ВАЖНО: регистрируем TCP-канал в роутере
        self.router.register(tcp)

        # Создаём остальные компоненты
        session = PcSession(sc.pc_id, primary=tcp, backup=serial)
        watcher = LogWatcher(sc.pc_id, sc.com_log)
        boot = BootAutomation(watcher, sc.login, sc.password)

        # Создаём Slot — единую сущность
        slot = Slot(
            pc_id=sc.pc_id,
            tcp_channel=tcp,
            serial_channel=serial,
            log_watcher=watcher,
            boot_automation=boot,
            pc_session=session,
        )

        self.slots[sc.pc_id] = slot

    def get(self, pc_id: str) -> Slot | None:
        return self.slots.get(pc_id)

    def all_pc_ids(self) -> list[str]:
        return list(self.slots.keys())