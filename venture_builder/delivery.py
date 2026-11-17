"""Delivery: one access token per order_id. Granting twice for the same order does nothing (idempotent);
revoking marks the token revoked. Called only from the webhook handler, inside its transaction."""

from __future__ import annotations

import secrets
import sqlite3

from .decision_log import now_iso


def grant(db: sqlite3.Connection, order_id: str) -> tuple[str, bool]:
    """Returns (token, newly_granted)."""
    row = db.execute("SELECT token FROM deliveries WHERE order_id=?", (order_id,)).fetchone()
    if row:
        return row[0], False
    token = secrets.token_urlsafe(16)
    db.execute("INSERT INTO deliveries VALUES (?,?,?,?,NULL)", (order_id, token, "active", now_iso()))
    return token, True


def revoke(db: sqlite3.Connection, order_id: str) -> bool:
    cur = db.execute("UPDATE deliveries SET status='revoked', revoked_at=? WHERE order_id=? AND status='active'",
                     (now_iso(), order_id))
    return cur.rowcount > 0


def has_access(db: sqlite3.Connection, token: str) -> bool:
    row = db.execute("SELECT status FROM deliveries WHERE token=?", (token,)).fetchone()
    return bool(row and row[0] == "active")
