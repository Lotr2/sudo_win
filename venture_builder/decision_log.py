"""Logging middleware: one decision_log.json entry per agent, tool or gate action.

Every entry always has the required keys `timestamp, agent, action, sources, model, cost, approved_by`.
Code logs through `log_action` (or the `logged` decorator) on the log bound to the current run, so no
agent can skip it.

Each run keeps its own log in `runs/<run_id>/decision_log.jsonl`, one JSON object per line, appended as it
happens (writing an entry costs the same however long the log is). `export_run` turns a run's log into the
JSON array committed as `decision_log.json`. Entry ids (`log_1`, `log_2`, ...) give the order within a run,
since several entries often share the same second.
"""

from __future__ import annotations

import contextvars
import functools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_KEYS = ("timestamp", "agent", "action", "sources", "model", "cost", "approved_by")

_current: contextvars.ContextVar["DecisionLog | None"] = contextvars.ContextVar("decision_log", default=None)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DecisionLog:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._entries: list[dict] = self._load()
        self.tags: dict = {}   # added to every entry, e.g. {"project": "real"}

    def _load(self) -> list[dict]:
        if not self.path:
            return []
        if self.path.exists():
            return read_log(self.path)
        legacy = self.path.with_suffix(".json")
        if self.path.suffix == ".jsonl" and legacy.exists():   # a run from before the JSON Lines format
            entries = read_log(legacy)
            self.path.write_text("".join(json.dumps(e) + "\n" for e in entries))
            return entries
        return []

    @property
    def entries(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def append(self, agent: str, action: str, *, sources=None, model=None, cost: float = 0,
               approved_by: str | None = None, **extra) -> dict:
        with self._lock:
            entry = {
                "id": f"log_{len(self._entries) + 1}",
                "timestamp": now_iso(),
                "agent": agent,
                "action": action,
                "sources": sorted({s for s in (sources or []) if s}),
                "model": model,
                "cost": cost,
                "approved_by": approved_by,
            }
            entry.update(self.tags)
            entry.update({k: v for k, v in extra.items() if v is not None})
            if agent == "founder" or approved_by == "founder":
                from .approval import simulation_enabled
                if simulation_enabled():
                    entry["simulated_founder"] = True   # made by scripts/simulate_founder.py, not a person
                else:
                    entry["real_founder"] = True        # a person, through the web UI or the terminal
            self._entries.append(entry)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            return entry

    def bind(self):
        """Make this the log for the current context (thread / task)."""
        return _current.set(self)


def read_log(path: Path | str) -> list[dict]:
    """A decision log file as a list of entries: JSON Lines (a run's log) or a JSON array (the exported log).
    A line cut off by a crash mid-write is skipped."""
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith("["):
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    entries = []
    for line in text.splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def current() -> DecisionLog:
    log = _current.get()
    if log is None:
        log = DecisionLog(None)
        _current.set(log)
    return log


def log_action(agent: str, action: str, **kwargs) -> dict:
    return current().append(agent, action, **kwargs)


def logged(agent: str, action: str | None = None):
    """Decorator for code steps and gates: logs one entry per call, with the outcome when there is one."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            result = fn(*args, **kwargs)
            extra = {}
            outcome = getattr(result, "outcome", None)
            if outcome is not None:
                extra["outcome"] = outcome
                extra["reasons"] = getattr(result, "reasons", None)
            log_action(agent, action or fn.__name__, **extra)
            return result

        return inner

    return wrap
