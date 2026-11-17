"""Durable run state (runs/<run>/state.json): what is done, so a crashed or paused run resumes where it stopped.

Every write is atomic (temp file + rename). Outward actions are recorded by idea+action id and never run twice.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class RunState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"version": 1, "meta": {}, "ideas": {}, "decisions": {}, "outward": {}}

    # ------------------------------------------------------------------ io
    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.path)

    def _idea(self, idea: str) -> dict:
        return self.data["ideas"].setdefault(idea, {"status": "pending", "steps": {}, "gates": {},
                                                    "payment_log": [], "invariants": {}})

    # --------------------------------------------------------------- meta
    def set_meta(self, **kw) -> None:
        with self._lock:
            self.data["meta"].update(kw)
            self.save()

    @property
    def meta(self) -> dict:
        return self.data["meta"]

    # ---------------------------------------------------------------- ideas
    def idea(self, idea: str) -> dict:
        with self._lock:
            return self._idea(idea)

    def set_idea(self, idea: str, **kw) -> None:
        with self._lock:
            self._idea(idea).update(kw)
            self.save()

    # ---------------------------------------------------------------- steps
    def get_step(self, idea: str, key: str) -> dict | None:
        with self._lock:
            return self._idea(idea)["steps"].get(key)

    def put_step(self, idea: str, key: str, rec: dict) -> None:
        with self._lock:
            self._idea(idea)["steps"][key] = rec
            self.save()

    def drop_phase(self, idea: str, phase: int) -> None:
        with self._lock:
            i = self._idea(idea)
            for k in [k for k in i["steps"] if k.startswith(f"phase{phase}_")]:
                del i["steps"][k]
            i["gates"].pop(str(phase), None)
            self.save()

    def get_gate(self, idea: str, phase: int) -> dict | None:
        with self._lock:
            return self._idea(idea)["gates"].get(str(phase))

    def put_gate(self, idea: str, phase: int, gate: dict) -> None:
        with self._lock:
            self._idea(idea)["gates"][str(phase)] = gate
            self.save()

    # --------------------------------------------------- founder decisions
    def get_decision(self, idea: str, action_id: str) -> dict | None:
        with self._lock:
            return self.data["decisions"].get(f"{idea}|{action_id}")

    def put_decision(self, idea: str, action_id: str, choice: str, payload_hash: str, source: str) -> None:
        with self._lock:
            self.data["decisions"][f"{idea}|{action_id}"] = {"choice": choice, "payload_hash": payload_hash,
                                                             "source": source, "at": time.time()}
            self.save()

    # ------------------------------------------------------ outward actions
    def outward_done(self, idea: str, action_id: str) -> dict | None:
        with self._lock:
            return self.data["outward"].get(f"{idea}|{action_id}")

    def mark_outward(self, idea: str, action_id: str, **info) -> None:
        with self._lock:
            self.data["outward"][f"{idea}|{action_id}"] = {"at": time.time(), **info}
            self.save()
