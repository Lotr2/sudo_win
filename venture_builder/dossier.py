"""Venture Dossier (the blackboard) and the per-run store in `runs/<run_id>/`.

The dossier is one JSON record, saved after every step and versioned per phase
(`dossier.json`, `dossier_phase<N>.json`). Each agent receives only the fields its task needs.
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from .decision_log import DecisionLog, now_iso
from .schemas import (
    DraftProfile, GateResult, InvestorReview, LaunchDrafts, LeanCanvas, PolicyDrafts, Pricing, Spec,
    UserDocs, Variant, VariantScore,
)

BRIEF_FIELDS = ["idea", "skills", "budget_usd", "hours_per_week", "market", "values"]


class Dossier(BaseModel):
    run_id: str
    brief: str
    phase: int = 1
    status: str = "running"  # running | stopped | finished
    profile: DraftProfile | None = None
    readback_confirmed: bool = False
    policy_block: str | None = None
    variants: list[Variant] = []
    findings: dict[str, list[dict]] = {}
    search_failed: bool = False
    scores: list[VariantScore] = []
    chosen_variant: str | None = None
    interview_notes: str | None = None
    founder_inputs: list[dict] = []
    canvas: LeanCanvas | None = None
    review: InvestorReview | None = None
    objection_answers: list[dict] = []
    experiment: dict | None = None
    locked_targets: dict | None = None
    lock_hash: str | None = None
    signups: dict | None = None
    landing_item: str | None = None
    spec: Spec | None = None
    build: dict = {}
    docs: UserDocs | None = None
    pricing: Pricing | None = None
    policies: PolicyDrafts | None = None
    lint: list[str] = []
    launch: LaunchDrafts | None = None
    launch_items: list[str] = []
    channels: list[str] = []
    payment_test: dict = {}
    metrics: dict = {}
    gate6_targets: dict | None = None
    gate6_hash: str | None = None
    support: list[dict] = []
    memo: dict | None = None
    gates: dict[str, GateResult] = {}
    gate_attempts: dict[str, int] = {}
    lessons: list[str] = []
    gate1_explained: dict = {}
    brand: dict = {}
    research_log: dict = {}        # per version and researcher: queries tried, those that found evidence, dead ends
    shortlist: dict = {}           # Stage 1 idea shortlist: kept and dropped versions with their pre-screen signal               # founder's brand for the landing page: name, color (#hex), logo (data URI)     # plain-words score breakdown and other directions shown at Gate 1
    counters: dict[str, int] = {}
    asked: list[str] = []
    history: list[dict] = []

    def bump(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def count(self, key: str) -> int:
        return self.counters.get(key, 0)

    def slice(self, *fields: str) -> dict:
        """The part of the dossier an agent sees."""
        data = self.model_dump(include=set(fields), mode="json")
        return {k: v for k, v in data.items() if v not in (None, [], {})}


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


class RunStore:
    """Everything one run writes, under runs/<run_id>/ (git-ignored)."""

    def __init__(self, runs_dir: Path, run_id: str):
        self.run_id = run_id
        self.dir = Path(runs_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "outputs").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self.log = DecisionLog(self.dir / "decision_log.json")

    @property
    def outputs(self) -> Path:
        return self.dir / "outputs"

    def path(self, name: str) -> Path:
        return self.dir / name

    def save(self, dossier: Dossier) -> None:
        with self._lock:
            data = dossier.model_dump_json(indent=1)
            (self.dir / "dossier.json").write_text(data)
            (self.dir / f"dossier_phase{dossier.phase}.json").write_text(data)

    def load(self) -> Dossier:
        return Dossier.model_validate_json((self.dir / "dossier.json").read_text())

    def exists(self) -> bool:
        return (self.dir / "dossier.json").exists()

    def converse(self, speaker: str, text: str, *, phase: int, kind: str = "message", fields: list[str] | None = None) -> dict:
        turn = {"id": f"turn_{self._count('conversation.jsonl') + 1}", "timestamp": now_iso(), "speaker": speaker,
                "kind": kind, "phase": phase, "text": text, "fields": fields or []}
        self._append("conversation.jsonl", turn)
        self.progress(f"{speaker}: {text}", kind="chat", speaker=speaker)
        return turn

    def conversation(self) -> list[dict]:
        return self._read_jsonl("conversation.jsonl")

    def progress(self, message: str, kind: str = "progress", **extra) -> None:
        self._append("progress.jsonl", {"timestamp": now_iso(), "kind": kind, "message": message, **extra})

    def progress_events(self, start: int = 0) -> list[dict]:
        return self._read_jsonl("progress.jsonl")[start:]

    def write_output(self, name: str, content: str) -> Path:
        p = self.outputs / name
        p.write_text(content)
        self.progress(f"wrote outputs/{name}", kind="output", file=name)
        return p

    # ---- helpers ----
    def _append(self, name: str, row: dict) -> None:
        with self._lock:
            with (self.dir / name).open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read_jsonl(self, name: str) -> list[dict]:
        p = self.dir / name
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def _count(self, name: str) -> int:
        p = self.dir / name
        return sum(1 for _ in p.open()) if p.exists() else 0
