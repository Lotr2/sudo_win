"""Founder approval, enforced in code.

Only an ApprovalBroker can mint a Receipt, and only after a founder decision from the UI, the CLI,
or the founder-authored decisions file. Every outward action is wrapped in @requires_approval and
raises without a valid, unused receipt whose payload hash matches the content being acted on.
There is no auto-approve path: a missing decision pauses the run.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SECRET = secrets.token_bytes(32)
_USED: set[str] = set()
_USED_LOCK = threading.Lock()

APPROVE, REJECT = "approve", "reject"
GATE_CHOICES = ("accept", "go", "repeat", "revise", "redo", "reset", "pivot", "stop")   # per gate: gates.choices()


class ApprovalRequired(PermissionError):
    """An outward action was attempted without a valid founder receipt."""


class ApprovalPending(RuntimeError):
    """No founder decision is available; the run must pause."""


def payload_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Receipt:
    action_id: str
    kind: str
    payload_hash: str
    nonce: str
    sig: str

    def _expected_sig(self) -> str:
        msg = f"{self.action_id}|{self.kind}|{self.payload_hash}|{self.nonce}".encode()
        return hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()


def _mint(action_id: str, kind: str, content: str) -> Receipt:
    nonce = secrets.token_hex(8)
    r = Receipt(action_id, kind, payload_hash(content), nonce, "")
    return Receipt(action_id, kind, r.payload_hash, nonce, r._expected_sig())


def consume(receipt: Receipt | None, kind: str, content: str) -> None:
    """Validate and spend a receipt. Raises ApprovalRequired on any problem."""
    if not isinstance(receipt, Receipt):
        raise ApprovalRequired(f"{kind}: no founder approval receipt")
    if not hmac.compare_digest(receipt.sig, receipt._expected_sig()):
        raise ApprovalRequired(f"{kind}: receipt signature invalid")
    if receipt.kind != kind:
        raise ApprovalRequired(f"{kind}: receipt is for {receipt.kind}")
    if receipt.payload_hash != payload_hash(content):
        raise ApprovalRequired(f"{kind}: content changed after approval")
    with _USED_LOCK:
        if receipt.nonce in _USED:
            raise ApprovalRequired(f"{kind}: receipt already used")
        _USED.add(receipt.nonce)


def requires_approval(kind: str):
    """Decorated functions must be called with keyword args `content=` and `receipt=`."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, content: str, receipt: Receipt | None = None, **kwargs):
            consume(receipt, kind, content)
            return fn(*args, content=content, **kwargs)
        wrapper.__requires_approval__ = kind
        return wrapper
    return deco


@dataclass
class Decision:
    choice: str          # approve / reject, or a gate choice
    source: str          # decisions_file / ui / cli
    note: str = ""


class ApprovalBroker:
    """Collects founder decisions and mints receipts. One broker per run."""

    def __init__(self, log, decisions: dict | None = None, queue_dir: Path | None = None,
                 interactive: bool = False, timeout_s: float = 3600, state=None):
        self.log = log
        self.decisions = decisions or {}
        self.queue_dir = Path(queue_dir) if queue_dir else None
        self.interactive = interactive
        self.timeout_s = timeout_s
        self.state = state          # core.state.RunState: audit record of decisions (never replayed)
        self._cli_lock = threading.Lock()

    # -- decision sources -------------------------------------------------
    # A decision is asked for ONE attempt and never reused for another one. In the decisions file the plain
    # key ("phase3.gate") answers attempt 1 only; attempt n needs "phase3.gate@n". Values are a choice string
    # or {choice: ..., note: ...}.
    @staticmethod
    def file_key(action_id: str, attempt: int) -> str:
        return action_id if attempt <= 1 else f"{action_id}@{attempt}"

    def _from_file(self, idea: str, action_id: str, attempt: int) -> Decision | None:
        value = (self.decisions.get(idea) or {}).get(self.file_key(action_id, attempt))
        if value is None:
            return None
        if isinstance(value, dict):
            return Decision(str(value.get("choice", "")), "decisions_file", str(value.get("note", "") or ""))
        return Decision(str(value), "decisions_file")

    def _from_queue(self, idea: str, action_id: str, kind: str, summary: str, content: str, attempt: int,
                    choices: list[str]) -> Decision | None:
        if not self.queue_dir:
            return None
        pending = self.queue_dir / "pending"
        decided = self.queue_dir / "decided"
        pending.mkdir(parents=True, exist_ok=True)
        decided.mkdir(parents=True, exist_ok=True)
        rid = f"{idea}__{action_id}" + (f"__a{attempt}" if attempt > 1 else "")
        rid = rid.replace("/", "_")
        (pending / f"{rid}.json").write_text(json.dumps({
            "id": rid, "idea": idea, "action_id": action_id, "kind": kind, "attempt": attempt, "choices": choices,
            "summary": summary, "content": content[:4000], "requested_at": time.time()}), encoding="utf-8")
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            f = decided / f"{rid}.json"
            if f.exists():
                d = json.loads(f.read_text(encoding="utf-8"))
                f.unlink()
                (pending / f"{rid}.json").unlink(missing_ok=True)
                return Decision(d["choice"], "ui", d.get("note", "") or "")
            time.sleep(0.5)
        return None

    def _from_cli(self, action_id: str, summary: str, attempt: int, choices: list[str]) -> Decision | None:
        if not self.interactive:
            return None
        with self._cli_lock:          # parallel ideas must not interleave terminal prompts
            answer = input(f"\n[FOUNDER] {action_id} (attempt {attempt}): {summary}\n  {'/'.join(choices)}? ").strip().lower()
            note = input("  note for the agents (optional): ").strip() if answer in ("revise", "redo", "reset") else ""
        return Decision(answer, "cli", note) if answer else None

    def decide(self, idea: str, action_id: str, kind: str, summary: str, content: str, attempt: int = 1,
               choices: list[str] | None = None) -> Decision:
        valid = list(choices or (GATE_CHOICES if kind == "gate" else (APPROVE, REJECT)))
        d = (self._from_file(idea, action_id, attempt)
             or self._from_queue(idea, action_id, kind, summary, content, attempt, valid)
             or self._from_cli(action_id, summary, attempt, valid))
        if d is None:
            self.log.append("founder", f"awaiting:{action_id}", approved_by=None, idea=idea, attempt=attempt)
            raise ApprovalPending(f"no founder decision for {idea} {self.file_key(action_id, attempt)}")
        if d.choice not in valid:
            self.log.append("founder", f"invalid:{action_id}", approved_by=None, idea=idea, attempt=attempt,
                            choice=d.choice, allowed=valid)
            raise ApprovalPending(f"founder decision {d.choice!r} for {action_id} (attempt {attempt}) is not one of {valid}")
        self.log.append("founder", f"{d.choice}:{action_id}", approved_by="founder", idea=idea, attempt=attempt,
                        input_source=d.source, payload_hash=payload_hash(content), note=d.note or None)
        if self.state:          # recorded for the audit trail only; never replayed
            self.state.put_decision(idea, f"{action_id}#{attempt}", d.choice, payload_hash(content), d.source)
        return d

    # -- receipts ----------------------------------------------------------
    def request(self, idea: str, action_id: str, kind: str, summary: str, content: str, attempt: int = 1) -> Receipt | None:
        """Returns a receipt if the founder approved, None if rejected. Raises ApprovalPending."""
        d = self.decide(idea, action_id, kind, summary, content, attempt)
        if d.choice == REJECT:
            return None
        return _mint(action_id, kind, content)


def load_decisions(path: str | os.PathLike | None) -> dict:
    if not path:
        return {}
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("decisions file must be a mapping of idea -> {action_id: decision}")
    return data
