"""
Board — физическая плата, которая может быть вставлена в слот.
Плата ничего не знает о себе. Её состояние наблюдается со стороны:
  - через каналы связи (подключена ли)
  - через лог-вотчер (загрузка биоса, ОС)
  - через автоматизацию логина (успешный вход в шелл)

В будущем:
  - плата идентифицируется по серийнику (считывается слотом)
  - может переезжать между слотами
  - хранит историю для БД
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Board:
    board_id: str  # будущий серийник, пока заглушка = slot_id слота

    bios: bool = False
    os: bool = False
    shell: bool = False

    last_seen: datetime | None = None

    def reset(self) -> None:
        """Сброс наблюдаемого состояния (перезагрузка, выключение, смена платы)."""
        self.bios = False
        self.os = False
        self.shell = False
        self.last_seen = None

    def touch(self) -> None:
        """Обновить время последнего наблюдения."""
        self.last_seen = datetime.now()