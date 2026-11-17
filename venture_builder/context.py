"""Everything a department step needs for one run: settings, router, stores, ledgers, outbox, guard,
tools. Steps return `Next(node)` to move on or `Ask(...)` to pause for the founder."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from .approval import Outbox
from .assumptions import AssumptionLedger
from .config import Settings
from .dossier import BRIEF_FIELDS, Dossier, RunStore
from .ledger import EvidenceLedger
from .policy_guard import PolicyGuard
from .router import Router


@dataclass
class Next:
    node: str


@dataclass
class Ask:
    """Pause for the founder. kinds: question, readback, notes, approval, gate, mocked_input."""
    kind: str
    payload: dict
    resume_to: str
    timeout: int | None = None

    def as_dict(self, phase: int) -> dict:
        return {"kind": self.kind, "phase": phase, "timeout": self.timeout, **self.payload}


END = "end"


@dataclass
class Context:
    settings: Settings
    router: Router
    store: RunStore
    search: Any
    fetch: Any
    dossier: Dossier = None  # type: ignore[assignment]
    ledger: EvidenceLedger = None  # type: ignore[assignment]
    assumptions: AssumptionLedger = None  # type: ignore[assignment]
    outbox: Outbox = None  # type: ignore[assignment]
    guard: PolicyGuard = None  # type: ignore[assignment]
    node_started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        self.ledger = EvidenceLedger(self.store.path("ledger.json"))
        self.assumptions = AssumptionLedger(self.store.path("assumptions.json"))
        self.outbox = Outbox(self.store.path("outbox.db"))
        self.guard = PolicyGuard(self.router)
        if self.dossier is None and self.store.exists():
            self.dossier = self.store.load()

    # ---- settings shortcuts ----
    @property
    def t(self) -> dict:
        """The thresholds this run started with. They are snapshotted into the dossier and hashed into the
        decision log at the start, so editing config/thresholds.toml never changes a run in progress, and a
        snapshot edited afterwards is refused."""
        d = self.dossier
        if d is None or not d.thresholds:
            return self.settings.thresholds()
        from .gates import LockedTargets
        return LockedTargets(d.thresholds, d.thresholds_hash or "").verify("the run's thresholds")

    def lock_thresholds(self, note: str | None = None) -> None:
        """Snapshot config/thresholds.toml into the dossier and log its hash (once per run)."""
        if self.dossier is None or self.dossier.thresholds:
            return
        from .decision_log import log_action
        from .gates import LockedTargets
        locked = LockedTargets.lock(self.settings.thresholds())
        self.dossier.thresholds, self.dossier.thresholds_hash = locked.targets, locked.hash
        self.save()
        log_action("threshold_lock", "lock_thresholds", hash=locked.hash, note=note)

    @property
    def demo(self) -> bool:
        return self.settings.demo

    def question_timeout(self) -> int | None:
        return self.t["demo"]["question_timeout_seconds"] if self.demo else None

    def blocking_limit(self) -> int:
        return self.t["demo"]["max_blocking_questions_per_phase"] if self.demo else self.t["limits"]["max_blocking_questions"]

    def over_budget(self, seconds: float) -> bool:
        """Demo mode: per-node time budget."""
        return self.demo and time.monotonic() - self.node_started > seconds

    @contextlib.contextmanager
    def bound(self):
        token = self.store.log.bind()
        self.node_started = time.monotonic()
        try:
            yield
        finally:
            from .decision_log import _current
            _current.reset(token)

    # ---- agentic skill settings ----
    def skill(self, name: str):
        """A [skills] setting from config/thresholds.toml, using the *_demo value in demo mode."""
        sk = self.t.get("skills", {})
        if self.demo and f"{name}_demo" in sk:
            return sk[f"{name}_demo"]
        return sk.get(name)

        # ---- conversation helpers ----
    def say(self, text: str, kind: str = "message") -> None:
        self.store.converse("liaison", text, phase=self.dossier.phase, kind=kind)

    def note(self, text: str) -> None:
        self.store.progress(text)

    def save(self) -> None:
        self.store.save(self.dossier)

    def brief_fields(self) -> list[str]:
        return BRIEF_FIELDS

    def author_family(self, role: str) -> str:
        return self.router.author_family(role)
