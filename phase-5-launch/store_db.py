"""SQLite store for orders, payments, deliveries, refunds and payouts (standard library only).

Amounts are integer cents. Buyer emails are personal data: the database lives in the git-ignored runtime folder.
UNIQUE constraints carry the idempotency guarantees: one processed row per webhook event id, one payment per
order, one access grant per order.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, email TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT NOT NULL, checkout_id TEXT UNIQUE, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, order_id TEXT, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE, amount_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL,
  refunded_cents INTEGER NOT NULL DEFAULT 0, payout_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS grants (
  order_id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS refunds (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL, reason TEXT,
  requested_at TEXT NOT NULL, decided_at TEXT, approved_by TEXT);
CREATE TABLE IF NOT EXISTS payouts (
  id TEXT PRIMARY KEY, amount_cents INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, kind TEXT NOT NULL, to_email TEXT NOT NULL,
  subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mock_checkouts (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


class tx:
    """`with tx(db):` runs the block in one transaction (BEGIN IMMEDIATE ... COMMIT / ROLLBACK)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, *_):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def one(db, sql: str, *args):
    return db.execute(sql, args).fetchone()


def all_rows(db, sql: str, *args) -> list:
    return db.execute(sql, args).fetchall()
