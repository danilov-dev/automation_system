from typing import Protocol, Iterator, runtime_checkable
from pathlib import Path
import logging
from .test_result import TestResult

logger = logging.getLogger(__name__)


@runtime_checkable
class ResultStorage(Protocol):
    """Порт хранилища. Раннер знает только этот контракт."""
    def save(self, result: TestResult) -> None: ...
    def load(self, slot_id: str) -> Iterator[TestResult]: ...


class JsonlResultStorage:
    """JSONL: одна строка — один результат. Дописывание без перезаписи файла."""

    def __init__(self, base_dir: str | Path = "data/reports"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, slot_id: str) -> Path:
        d = self.base_dir / slot_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "history.jsonl"

    def save(self, result: TestResult) -> None:
        try:
            with self._path(result.slot_id).open("a", encoding="utf-8") as f:
                f.write(result.to_json() + "\n")
        except Exception as e:
            logger.error(f"[storage] save failed: {e}")

    def load(self, slot_id: str) -> Iterator[TestResult]:
        import json
        p = self._path(slot_id)
        if not p.exists():
            return
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield TestResult(**json.loads(line))
                    except Exception as e:
                        logger.warning(f"[storage] bad line: {e}")


class NullResultStorage:
    """Заглушка для тестов — ничего не сохраняет."""
    def save(self, result: TestResult) -> None: pass
    def load(self, slot_id: str) -> Iterator[TestResult]: return iter(())