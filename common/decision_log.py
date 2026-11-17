"""Append-only decision log: one entry per agent action, in the format the organizers' checker reads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class DecisionLog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> list:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text() or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def append(
        self,
        agent: str,
        action: str,
        *,
        sources: list[str] | None = None,
        model: str | None = None,
        cost: float = 0,
        approved_by: str | None = None,
        **extra,
    ) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent": agent,
            "action": action,
            "sources": list(sources or []),
            "model": model,
            "cost": cost,
            "approved_by": approved_by,
            **extra,
        }
        entries = self._read()
        entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=2) + "\n")
        return entry

    def entries(self) -> list:
        return self._read()
