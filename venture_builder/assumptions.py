"""Assumption Ledger: every assumption, its status (assumed / confirmed / corrected) and the decisions
that depend on it. A correction returns the dependent decisions so only those steps re-run."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from pydantic import BaseModel

from .decision_log import now_iso


class Assumption(BaseModel):
    id: str
    field: str
    value: str
    status: str = "assumed"  # assumed | confirmed | corrected
    source: str = "system"  # system | founder_reported
    depends_on: list[str] = []
    critical: bool = False
    asked_at: str | None = None
    answer: str | None = None


class AssumptionLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.items: dict[str, Assumption] = {}
        if self.path and self.path.exists():
            for raw in json.loads(self.path.read_text() or "[]"):
                a = Assumption.model_validate(raw)
                self.items[a.id] = a

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([a.model_dump() for a in self.items.values()], indent=1))

    def by_field(self, field: str) -> Assumption | None:
        for a in self.items.values():
            if a.field == field:
                return a
        return None

    def add(self, field: str, value, *, depends_on: list[str] | None = None, status: str = "assumed",
            source: str = "system") -> Assumption:
        with self._lock:
            existing = self.by_field(field)
            if existing:
                existing.value = str(value)
                existing.depends_on = sorted(set(existing.depends_on) | set(depends_on or []))
                if status != "assumed":
                    existing.status, existing.source = status, source
                self._save()
                return existing
            a = Assumption(id=f"as_{len(self.items) + 1}", field=field, value=str(value), status=status,
                           source=source, depends_on=depends_on or [])
            self.items[a.id] = a
            self._save()
            return a

    def confirm(self, field: str, value=None, answer: str | None = None) -> Assumption:
        with self._lock:
            a = self.by_field(field)
            if a is None:
                a = Assumption(id=f"as_{len(self.items) + 1}", field=field, value=str(value))
                self.items[a.id] = a
            if value is not None:
                a.value = str(value)
            a.status, a.source, a.answer = "confirmed", "founder_reported", answer
            self._save()
            return a

    def correct(self, field: str, value, answer: str | None = None) -> list[str]:
        """Record a founder correction; returns the decisions that must be re-run."""
        with self._lock:
            a = self.by_field(field)
            if a is None:
                a = Assumption(id=f"as_{len(self.items) + 1}", field=field, value=str(value))
                self.items[a.id] = a
            a.value, a.status, a.source, a.answer = str(value), "corrected", "founder_reported", answer
            self._save()
            return list(a.depends_on)

    def depend(self, field: str, decision: str) -> None:
        with self._lock:
            a = self.by_field(field)
            if a and decision not in a.depends_on:
                a.depends_on.append(decision)
                self._save()

    def mark_critical(self, field: str, critical: bool = True) -> None:
        with self._lock:
            a = self.by_field(field)
            if a:
                a.critical = critical
                a.asked_at = a.asked_at or now_iso()
                self._save()

    def open(self) -> list[Assumption]:
        return [a for a in self.items.values() if a.status == "assumed"]

    def rests_on(self, decision: str) -> list[Assumption]:
        return [a for a in self.items.values() if decision in a.depends_on]

    def critical_unconfirmed(self, decision: str) -> list[Assumption]:
        return [a for a in self.rests_on(decision) if a.critical and a.status == "assumed"]

    def confirmed_share(self, fields: list[str]) -> tuple[int, int]:
        done = sum(1 for f in fields if (a := self.by_field(f)) and a.status in {"confirmed", "corrected"})
        return done, len(fields)

    def rests_on_markdown(self, decision: str, brief_fields: list[str]) -> str:
        from . import labels
        done, total = self.confirmed_share(brief_fields)
        lines = ["## What this is based on", ""]
        items = self.rests_on(decision)
        if not items:
            lines.append("- Nothing we're still guessing.")
        for a in items:
            flag = " (the recommendation depends on this)" if a.critical else ""
            label = {"assumed": "a guess, not confirmed yet (ASSUMPTION)", "confirmed": "confirmed by you",
                     "corrected": "corrected by you"}[a.status]
            lines.append(f"- Your {labels.field_name(a.field)}: {labels.field_value(a.field, a.value)} ({label}){flag}")
        lines += ["", f"{done} of {total} details about you are confirmed."]
        return "\n".join(lines)
