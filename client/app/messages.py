"""
Классы сообщений для клиента (дублируют хостовые для независимости).
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class Task:
    task_id: str
    command: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "task",
            "task_id": self.task_id,
            "command": self.command,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            task_id=data["task_id"],
            command=data["command"],
            params=data.get("params", {}),
        )


@dataclass
class Report:
    task_id: str
    command: str
    status: TaskStatus
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "type": "report",
            "task_id": self.task_id,
            "command": self.command,
            "status": self.status.value,
            "timestamp": self.timestamp,
        }
        if self.status == TaskStatus.ERROR and self.error:
            data["error"] = self.error
        else:
            data["result"] = self.result
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Report":
        return cls(
            task_id=data["task_id"],
            command=data.get("command", ""),
            status=TaskStatus(data["status"]),
            result=data.get("result", {}),
            error=data.get("error"),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )