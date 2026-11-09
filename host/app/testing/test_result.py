from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional
import json


@dataclass
class TestResult:
    """Результат одного теста на одном слоте."""
    slot_id: str
    command: str
    status: str                      # success | error | timeout | cancelled | skipped
    task_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None

    @property
    def duration_s(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        d["duration_s"] = self.duration_s
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)