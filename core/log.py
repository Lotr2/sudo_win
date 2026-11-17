"""decision_log.json writer: one entry per agent action, all required keys always present."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.conventions import LOG_KEYS


class LogSchemaError(ValueError):
    pass


def validate_entry(entry: dict) -> None:
    missing = [k for k in LOG_KEYS if k not in entry]
    if missing:
        raise LogSchemaError(f"missing keys {missing}")
    if not isinstance(entry["action"], str) or not entry["action"]:
        raise LogSchemaError("action must be a non-empty string")
    if not isinstance(entry["sources"], list) or not all(isinstance(s, str) and s.startswith("http") for s in entry["sources"]):
        raise LogSchemaError("sources must be a list of URLs")
    if entry["model"] is not None and not isinstance(entry["model"], str):
        raise LogSchemaError("model must be a string or null")
    if not isinstance(entry["cost"], (int, float)) or isinstance(entry["cost"], bool):
        raise LogSchemaError("cost must be a number")
    if entry["approved_by"] not in (None, "founder"):
        raise LogSchemaError('approved_by must be "founder" or null')


class DecisionLog:
    def __init__(self, path: Path, append: bool = False):
        """append=True continues an existing log (resumed run); otherwise starts a new one."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if append and self.path.exists():
            self.entries: list[dict] = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.entries = []
            self.path.write_text("[]\n", encoding="utf-8")

    def append(self, agent: str, action: str, *, sources=None, model=None, cost=0, approved_by=None, **extra) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent": agent,
            "action": action,
            "sources": list(dict.fromkeys(sources or [])),
            "model": model,
            "cost": cost,
            "approved_by": approved_by,
            **extra,
        }
        validate_entry(entry)
        with self._lock:
            self.entries.append(entry)
            self.path.write_text(json.dumps(self.entries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return entry
