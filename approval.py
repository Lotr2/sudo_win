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
GATE_CHOICES = ("accept", "go", "repeat", "pivot", "stop")


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
        self.state = state          # core.state.RunState: founder decisions survive a crash/resume
        self._cli_lock = threading.Lock()

    # -- decision sources -------------------------------------------------
    def _from_file(self, idea: str, action_id: str) -> Decision | None:
        value = (self.decisions.get(idea) or {}).get(action_id)
        return Decision(str(value), "decisions_file") if value is not None else None

    def _from_queue(self, idea: str, action_id: str, kind: str, summary: str, content: str) -> Decision | None:
        if not self.queue_dir:
            return None
        pending = self.queue_dir / "pending"
        decided = self.queue_dir / "decided"
        pending.mkdir(parents=True, exist_ok=True)
        decided.mkdir(parents=True, exist_ok=True)
        rid = f"{idea}__{action_id}".replace("/", "_")
        (pending / f"{rid}.json").write_text(json.dumps({
            "id": rid, "idea": idea, "action_id": action_id, "kind": kind,
            "summary": summary, "content": content[:4000], "requested_at": time.time()}), encoding="utf-8")
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            f = decided / f"{rid}.json"
            if f.exists():
                d = json.loads(f.read_text(encoding="utf-8"))
                f.unlink()
                (pending / f"{rid}.json").unlink(missing_ok=True)
                return Decision(d["choice"], "ui", d.get("note", ""))
            time.sleep(0.5)
        return None

    def _from_cli(self, action_id: str, kind: str, summary: str) -> Decision | None:
        if not self.interactive:
            return None
        options = "/".join(GATE_CHOICES) if kind == "gate" else "approve/reject"
        with self._cli_lock:          # parallel ideas must not interleave terminal prompts
            answer = input(f"\n[FOUNDER] {action_id}: {summary}\n  {options}? ").strip().lower()
        return Decision(answer, "cli") if answer else None

    def _from_state(self, idea: str, action_id: str, content: str) -> Decision | None:
        """A decision the founder already made for exactly this content (resumed run)."""
        prev = self.state.get_decision(idea, action_id) if self.state else None
        if prev and prev["payload_hash"] == payload_hash(content):
            return Decision(prev["choice"], f"resume:{prev['source']}")
        return None

    def decide(self, idea: str, action_id: str, kind: str, summary: str, content: str) -> Decision:
        d = (self._from_state(idea, action_id, content)
             or self._from_file(idea, action_id)
             or self._from_queue(idea, action_id, kind, summary, content)
             or self._from_cli(action_id, kind, summary))
        if d is None:
            self.log.append("founder", f"awaiting:{action_id}", approved_by=None, idea=idea)
            raise ApprovalPending(f"no founder decision for {idea} {action_id}")
        valid = GATE_CHOICES if kind == "gate" else (APPROVE, REJECT)
        if d.choice not in valid:
            raise ApprovalPending(f"invalid founder decision {d.choice!r} for {action_id}")
        self.log.append("founder", f"{d.choice}:{action_id}", approved_by="founder", idea=idea,
                        input_source=d.source, payload_hash=payload_hash(content))
        if self.state and not d.source.startswith("resume:"):
            self.state.put_decision(idea, action_id, d.choice, payload_hash(content), d.source)
        return d

    # -- receipts ----------------------------------------------------------
    def request(self, idea: str, action_id: str, kind: str, summary: str, content: str) -> Receipt | None:
        """Returns a receipt if the founder approved, None if rejected. Raises ApprovalPending."""
        d = self.decide(idea, action_id, kind, summary, content)
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
