"""Event store for sale to cash and Phase 6 (SQLite, append-only event table). Every event is MOCKED:
there is no payment provider, no card data and no real money."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from pathlib import Path

from .decision_log import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, type TEXT, order_id TEXT, amount REAL, fee REAL,
  payload TEXT, created_at TEXT, mocked INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, product TEXT, amount REAL, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS deliveries (order_id TEXT PRIMARY KEY, token TEXT UNIQUE, status TEXT, granted_at TEXT,
  revoked_at TEXT);
CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY, processed_at TEXT);
CREATE TABLE IF NOT EXISTS payouts (payout_id TEXT PRIMARY KEY, amount REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, value REAL, text TEXT,
  created_at TEXT, mocked INTEGER DEFAULT 1);
CREATE TRIGGER IF NOT EXISTS events_append_only BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
"""


class EventStore:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @contextlib.contextmanager
    def transaction(self):
        """A write transaction holding SQLite's write lock from the start, so sequential ids (ord_001, evt_0001)
        stay unique even when two processes share the event store. Nested use joins the outer transaction."""
        with self.lock:
            if self.db.in_transaction:
                yield self.db
                return
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def next_id(self, table: str, prefix: str, width: int) -> str:
        """Next sequential id for `table`; call inside `transaction()`."""
        n = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
        return f"{prefix}{n:0{width}d}"

    def append(self, type_: str, order_id: str | None, amount: float = 0, fee: float = 0, payload: dict | None = None,
               event_id: str | None = None) -> dict:
        """Append an event. With an explicit `event_id` that already exists, the stored event is returned
        unchanged (idempotent producer), so a retried emit can't create a second event."""
        with self.transaction():
            if event_id:
                row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
                if row:
                    return dict(row)
            ev = {"event_id": event_id or self.next_id("events", "evt_", 4), "type": type_, "order_id": order_id,
                  "amount": amount, "fee": fee, "payload": json.dumps(payload or {}), "created_at": now_iso(),
                  "mocked": 1}
            self.db.execute("INSERT INTO events VALUES (:event_id,:type,:order_id,:amount,:fee,:payload,:created_at,:mocked)", ev)
            return ev

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def add_activity(self, kind: str, value: float = 0, text: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT INTO activity (kind, value, text, created_at, mocked) VALUES (?,?,?,?,1)",
                            (kind, value, text, now_iso()))

    def copy_to(self, path: Path) -> "EventStore":
        """Scratch copy (used to plant a deliberately broken record for the reconciliation test)."""
        target = sqlite3.connect(str(path))
        self.db.backup(target)
        target.close()
        return EventStore(path)
