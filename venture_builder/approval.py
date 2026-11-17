"""Approval Outbox: agents only propose items; an executor runs only with a founder approval record.

- Each approval record is bound to the item's SHA-256 content hash. If the item changes after approval,
  the approval is invalid.
- Approval records and gate decisions are created only from a `FounderConfirmation`, which the CLI makes
  after an interactive TTY confirmation and the web UI makes from a button press. Chat text never reaches
  this module, and there is no auto-approve path.
- `publish()` raises `NotApproved` without a valid record. Every publish adapter is MOCKED.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .decision_log import log_action, now_iso

CHANNELS = {"cli", "ui"}

# A scripted founder for the committed demo run (scripts/simulate_founder.py). Its confirmations are accepted
# only when VENTURE_SIMULATED_FOUNDER=1 is set for that process, the web UI refuses to start with it, and every
# decision it makes is labelled simulated_founder in the decision log. Real use only ever accepts CLI or UI.
SIMULATION_CHANNEL = "simulation"
SIMULATION_ENV = "VENTURE_SIMULATED_FOUNDER"


def simulation_enabled() -> bool:
    import os
    return os.environ.get(SIMULATION_ENV) == "1"


def allowed_channels() -> set[str]:
    return CHANNELS | ({SIMULATION_CHANNEL} if simulation_enabled() else set())


class NotApproved(PermissionError):
    pass


class InvalidConfirmation(PermissionError):
    pass


def content_hash(content) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class FounderConfirmation:
    """Proof of an explicit founder action: the channel and the hash prefix the founder confirmed."""
    channel: str
    item_ref: str
    typed_hash_prefix: str

    def __post_init__(self):
        if self.channel not in allowed_channels():
            raise InvalidConfirmation(f"approvals come only from the CLI or the UI, not '{self.channel}'")
        if len(self.typed_hash_prefix) < 6:
            raise InvalidConfirmation("confirm with at least 6 characters of the item hash")


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, kind TEXT, action TEXT, title TEXT, content TEXT, content_hash TEXT,
  status TEXT, note TEXT, created_at TEXT, executed_at TEXT, phase INTEGER);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY, item_id TEXT, item_hash TEXT, action TEXT, approved_by TEXT,
  channel TEXT, timestamp TEXT);
CREATE TABLE IF NOT EXISTS gate_decisions (
  id TEXT PRIMARY KEY, gate INTEGER, attempt INTEGER, decision TEXT, recommended TEXT, variant_id TEXT,
  override INTEGER, decided_by TEXT, channel TEXT, timestamp TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS publish_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, approval_id TEXT, adapter TEXT, timestamp TEXT);
"""


class Outbox:
    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    # ---- items ----
    def propose(self, *, kind: str, action: str, title: str, content, phase: int) -> dict:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0] + 1
            item_id = f"ob_{n:02d}"
            self._db.execute(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, kind, action, title, json.dumps(content, ensure_ascii=False), content_hash(content),
                 "pending", None, now_iso(), None, phase))
            self._db.commit()
        log_action("approval_outbox", f"queue_{kind}", item_id=item_id)
        return self.get(item_id)

    def get(self, item_id: str) -> dict:
        row = self._db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        d = dict(row)
        d["content"] = json.loads(d["content"])
        return d

    def items(self, phase: int | None = None, status: str | None = None) -> list[dict]:
        q, args = "SELECT id FROM items WHERE 1=1", []
        if phase is not None:
            q += " AND phase=?"; args.append(phase)
        if status:
            q += " AND status=?"; args.append(status)
        return [self.get(r[0]) for r in self._db.execute(q + " ORDER BY id", args).fetchall()]

    def update_content(self, item_id: str, content) -> dict:
        """A revised draft gets a new hash, so any earlier approval no longer applies."""
        with self._lock:
            self._db.execute("UPDATE items SET content=?, content_hash=?, status='pending', note=NULL WHERE id=?",
                             (json.dumps(content, ensure_ascii=False), content_hash(content), item_id))
            self._db.commit()
        return self.get(item_id)

    # ---- founder actions (CLI / UI only) ----
    def approve(self, item_id: str, confirmation: FounderConfirmation) -> dict:
        item = self.get(item_id)
        if confirmation.item_ref != item_id:
            raise InvalidConfirmation("confirmation is for a different item")
        if not item["content_hash"].removeprefix("sha256:").startswith(confirmation.typed_hash_prefix.lower()):
            raise InvalidConfirmation("the typed hash does not match the item; it may have changed")
        if item["status"] not in {"pending"}:
            raise InvalidConfirmation(f"item is {item['status']}")
        record = {"approval_id": f"ap_{item_id}_{now_iso()}", "item_id": item_id, "item_hash": item["content_hash"],
                  "action": item["action"], "approved_by": "founder", "channel": confirmation.channel,
                  "timestamp": now_iso()}
        with self._lock:
            self._db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?)", tuple(record.values()))
            self._db.execute("UPDATE items SET status='approved' WHERE id=?", (item_id,))
            self._db.commit()
        log_action("founder", f"approve_{item['kind']}", approved_by="founder", item_id=item_id,
                   channel=confirmation.channel)
        return record

    def reject(self, item_id: str, note: str, channel: str) -> None:
        if channel not in allowed_channels():
            raise InvalidConfirmation("rejections come only from the CLI or the UI")
        with self._lock:
            self._db.execute("UPDATE items SET status='rejected', note=? WHERE id=?", (note, item_id))
            self._db.commit()
        log_action("founder", "reject_item", approved_by="founder", item_id=item_id, note=note[:200])

    def valid_approval(self, item_id: str) -> dict | None:
        item = self.get(item_id)
        row = self._db.execute(
            "SELECT * FROM approvals WHERE item_id=? AND item_hash=? ORDER BY timestamp DESC LIMIT 1",
            (item_id, item["content_hash"])).fetchone()
        return dict(row) if row else None

    def publish(self, item_id: str, adapter: str = "MOCKED") -> dict:
        """Executor: runs only with a valid approval record bound to the current content hash."""
        record = self.valid_approval(item_id)
        if record is None:
            log_action("publish_adapter", "publish_refused", item_id=item_id, reason="no valid approval record")
            raise NotApproved(f"{item_id}: no founder approval record for the current content")
        with self._lock:
            self._db.execute("INSERT INTO publish_log (item_id, approval_id, adapter, timestamp) VALUES (?,?,?,?)",
                             (item_id, record["approval_id"], adapter, now_iso()))
            self._db.execute("UPDATE items SET status='published', executed_at=? WHERE id=?", (now_iso(), item_id))
            self._db.commit()
        log_action("publish_adapter", "publish_MOCKED", approved_by="founder", item_id=item_id,
                   adapter=adapter)
        return self.get(item_id)

    def publish_log(self) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM publish_log ORDER BY id").fetchall()]

    # ---- gate decisions ----
    def record_gate_decision(self, *, gate: int, attempt: int, decision: str, recommended: str,
                             confirmation: FounderConfirmation, variant_id: str | None = None,
                             note: str = "") -> dict:
        expected = f"gate{gate}-{attempt}"
        if confirmation.item_ref != expected:
            raise InvalidConfirmation(f"confirmation is for {confirmation.item_ref}, not {expected}")
        if not gate_token(gate, attempt).startswith(confirmation.typed_hash_prefix.lower()):
            raise InvalidConfirmation("the typed code does not match this gate")
        row = {"id": f"gd_{gate}_{attempt}", "gate": gate, "attempt": attempt, "decision": decision,
               "recommended": recommended, "variant_id": variant_id, "override": int(decision != recommended),
               "decided_by": "founder", "channel": confirmation.channel, "timestamp": now_iso(), "note": note}
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO gate_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(row.values()))
            self._db.commit()
        log_action("founder", f"gate{gate}_decision", approved_by="founder", decision=decision,
                   recommended=recommended, override=bool(row["override"]), channel=confirmation.channel)
        return row

    def gate_decision(self, gate: int, attempt: int) -> dict | None:
        row = self._db.execute("SELECT * FROM gate_decisions WHERE gate=? AND attempt=?", (gate, attempt)).fetchone()
        return dict(row) if row else None


def gate_token(gate: int, attempt: int) -> str:
    """Short code the founder types to confirm a gate decision (like an item hash prefix)."""
    return hashlib.sha256(f"gate{gate}-{attempt}".encode()).hexdigest()
