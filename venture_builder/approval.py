"""Approval Outbox: agents only propose items; an executor runs only with a founder approval record.

- Each approval record is bound to the item's SHA-256 content hash. If the item changes after approval,
  the approval is invalid.
- Approval records and gate decisions are created only from a `FounderConfirmation`, which the CLI makes
  after an interactive TTY confirmation and the web UI makes from a button press. Chat text never reaches
  this module, and there is no auto-approve path.
- `publish()` raises `NotApproved` without a valid record. An approval is used once: the item moves from
  approved to published in one conditional update, so a second call raises `AlreadyExecuted`.
- Gate decisions are confirmed with a random one-time code issued when the gate is presented (not derivable
  from the gate number), stored here and spent by the decision.
- Every publish adapter is MOCKED.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
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


class AlreadyExecuted(NotApproved):
    """The approval was already used: an approved item runs at most once."""


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
CREATE TABLE IF NOT EXISTS gate_codes (
  gate INTEGER, attempt INTEGER, code TEXT, issued_at TEXT, used_at TEXT, PRIMARY KEY (gate, attempt));
"""


class Outbox:
    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        # One connection is shared by the web UI's request threads and the background step thread, so every
        # read and write goes through this lock (re-entrant: writes read the item back while holding it).
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    @contextlib.contextmanager
    def _write(self):
        """One write transaction holding SQLite's write lock from the start (BEGIN IMMEDIATE)."""
        if self._db.in_transaction:
            self._db.commit()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.rollback()
            raise
        self._db.commit()

    # ---- items ----
    def propose(self, *, kind: str, action: str, title: str, content, phase: int) -> dict:
        with self._lock, self._write():
            # BEGIN IMMEDIATE holds SQLite's write lock, so a CLI and the web UI on the same run can't pick
            # the same id.
            n = self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0] + 1
            item_id = f"ob_{n:02d}"
            self._db.execute(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, kind, action, title, json.dumps(content, ensure_ascii=False), content_hash(content),
                 "pending", None, now_iso(), None, phase))
        log_action("approval_outbox", f"queue_{kind}", item_id=item_id)
        return self.get(item_id)

    @staticmethod
    def _item(row) -> dict:
        d = dict(row)
        d["content"] = json.loads(d["content"])
        return d

    def get(self, item_id: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        return self._item(row)

    def items(self, phase: int | None = None, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM items WHERE 1=1", []
        if phase is not None:
            q += " AND phase=?"; args.append(phase)
        if status:
            q += " AND status=?"; args.append(status)
        with self._lock:   # one query, so the list is a consistent snapshot
            rows = self._db.execute(q + " ORDER BY id", args).fetchall()
        return [self._item(r) for r in rows]

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
        record = {"approval_id": f"ap_{uuid.uuid4().hex[:16]}", "item_id": item_id, "item_hash": item["content_hash"],
                  "action": item["action"], "approved_by": "founder", "channel": confirmation.channel,
                  "timestamp": now_iso()}
        with self._lock, self._write():
            cur = self._db.execute("UPDATE items SET status='approved' WHERE id=? AND status='pending' AND content_hash=?",
                                   (item_id, item["content_hash"]))
            if cur.rowcount == 0:
                raise InvalidConfirmation("the item changed or was decided while you were confirming")
            self._db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?)", tuple(record.values()))
        log_action("founder", f"approve_{item['kind']}", approved_by="founder", item_id=item_id,
                   channel=confirmation.channel)
        return record

    def reject(self, item_id: str, note: str, confirmation: FounderConfirmation) -> None:
        """Ask for changes. Same confirmation as an approval, so it is tied to the version the founder saw."""
        item = self.get(item_id)
        if confirmation.item_ref != item_id:
            raise InvalidConfirmation("confirmation is for a different item")
        if not item["content_hash"].removeprefix("sha256:").startswith(confirmation.typed_hash_prefix.lower()):
            raise InvalidConfirmation("the hash does not match the item; it may have changed")
        with self._lock:
            cur = self._db.execute("UPDATE items SET status='rejected', note=? WHERE id=? AND status IN "
                                   "('pending','approved')", (note, item_id))
            self._db.commit()
        if cur.rowcount == 0:
            raise InvalidConfirmation(f"item is {item['status']}")
        log_action("founder", "reject_item", approved_by="founder", item_id=item_id, note=note[:200],
                   channel=confirmation.channel)

    def valid_approval(self, item_id: str) -> dict | None:
        with self._lock:
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
        with self._lock, self._write():
            # Spend the approval: only one caller can move the item from approved to published.
            cur = self._db.execute("UPDATE items SET status='published', executed_at=? WHERE id=? AND status='approved' "
                                   "AND content_hash=?", (now_iso(), item_id, record["item_hash"]))
            if cur.rowcount == 0:
                executed = True
            else:
                executed = False
                self._db.execute("INSERT INTO publish_log (item_id, approval_id, adapter, timestamp) VALUES (?,?,?,?)",
                                 (item_id, record["approval_id"], adapter, now_iso()))
        if executed:
            log_action("publish_adapter", "publish_refused", item_id=item_id, reason="approval already used")
            raise AlreadyExecuted(f"{item_id}: already executed; an approval is used once")
        log_action("publish_adapter", "publish_MOCKED", approved_by="founder", item_id=item_id,
                   adapter=adapter)
        return self.get(item_id)

    def publish_log(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM publish_log ORDER BY id").fetchall()]

    # ---- gate decisions ----
    def record_gate_decision(self, *, gate: int, attempt: int, decision: str, recommended: str,
                             confirmation: FounderConfirmation, variant_id: str | None = None,
                             note: str = "") -> dict:
        expected = f"gate{gate}-{attempt}"
        if confirmation.item_ref != expected:
            raise InvalidConfirmation(f"confirmation is for {confirmation.item_ref}, not {expected}")
        row = {"id": f"gd_{gate}_{attempt}", "gate": gate, "attempt": attempt, "decision": decision,
               "recommended": recommended, "variant_id": variant_id, "override": int(decision != recommended),
               "decided_by": "founder", "channel": confirmation.channel, "timestamp": now_iso(), "note": note}
        with self._lock, self._write():
            # Spend the one-time code; a wrong, unknown or already-used code changes nothing.
            cur = self._db.execute("UPDATE gate_codes SET used_at=? WHERE gate=? AND attempt=? AND code=? "
                                   "AND used_at IS NULL", (now_iso(), gate, attempt,
                                                           confirmation.typed_hash_prefix.lower()))
            if cur.rowcount == 0:
                raise InvalidConfirmation("the typed code does not match this decision, or was already used")
            self._db.execute("INSERT INTO gate_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(row.values()))
        log_action("founder", f"gate{gate}_decision", approved_by="founder", decision=decision,
                   recommended=recommended, override=bool(row["override"]), channel=confirmation.channel)
        return row

    def gate_decision(self, gate: int, attempt: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM gate_decisions WHERE gate=? AND attempt=?", (gate, attempt)).fetchone()
        return dict(row) if row else None


    def issue_gate_code(self, gate: int, attempt: int) -> str:
        """A random one-time code the founder types to confirm this gate decision. Issued when the gate is
        presented; presenting the same attempt again replaces the code."""
        code = secrets.token_hex(3)
        with self._lock, self._write():
            self._db.execute("INSERT OR REPLACE INTO gate_codes VALUES (?,?,?,?,NULL)", (gate, attempt, code, now_iso()))
        return code
