"""
Registry — конструктор объектов на основе AppConfig.

Собирает на каждый слот:
  TcpChannel + SerialChannel → PcSession
  LogWatcher + BootAutomation

И один общий TcpRouter, в который регистрируются все TcpChannel.

НЕ запускает ничего. Старт/стоп — забота SessionManager.
"""
from dataclasses import dataclass

from app.channels.serial_channel import SerialChannel
from app.channels.tcp import TcpChannel
from app.channels.tcp_router import TcpRouter
from app.config import AppConfig, SlotConfig
from app.log_watcher import LogWatcher
from app.boot_automation import BootAutomation
from app.pc_session import PcSession
from app.utils.console_logger import ConsoleLogger, LogLevel


@dataclass
class SlotBundle:
    """Всё, что относится к одному тестируемому ПК."""
    pc_id: str
    tcp_channel: TcpChannel
    serial_channel: SerialChannel
    pc_session: PcSession
    log_watcher: LogWatcher
    boot_automation: BootAutomation


class Registry:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.router = TcpRouter(
            host=cfg.tcp.host,
            port=cfg.tcp.port,
            hello_timeout=cfg.tcp.hello_timeout,
        )
        self.slots: dict[str, SlotBundle] = {}
        for slot_cfg in cfg.slots.values():
            self._build_slot(slot_cfg)
        ConsoleLogger.write(
            f"[Registry] собрано слотов: {len(self.slots)}",
            LogLevel.INFO,
        )

    def _build_slot(self, sc: SlotConfig) -> None:
        tcp = TcpChannel(sc.pc_id)
        ser = SerialChannel(sc.pc_id, sc.com_ctrl)
        session = PcSession(sc.pc_id, primary=tcp, backup=ser)
        watcher = LogWatcher(sc.pc_id, sc.com_log)
        boot = BootAutomation(watcher, sc.login, sc.password)

        self.router.register(tcp)
        self.slots[sc.pc_id] = SlotBundle(
            pc_id=sc.pc_id,
            tcp_channel=tcp,
            serial_channel=ser,
            pc_session=session,
            log_watcher=watcher,
            boot_automation=boot,
        )

    def get(self, pc_id: str) -> SlotBundle | None:
        return self.slots.get(pc_id)

    def all_pc_ids(self) -> list[str]:
        return list(self.slots.keys())