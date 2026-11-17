"""Webhook handler for MOCKED payment events (idempotent consumer).

Each event is processed at most once (keyed by event_id) and each order is delivered at most once
(keyed by order_id). The event, the order status and the delivery change commit in one transaction.
"""

from __future__ import annotations

from . import delivery
from .decision_log import log_action, now_iso
from .events import EventStore


def handle(store: EventStore, event: dict) -> str:
    """Returns 'delivered', 'revoked', 'duplicate' or 'ignored'."""
    db = store.db
    with store.lock:
        db.execute("BEGIN IMMEDIATE")
        try:
            seen = db.execute("SELECT 1 FROM processed_events WHERE event_id=?", (event["event_id"],)).fetchone()
            if seen:
                db.execute("ROLLBACK")
                log_action("webhooks", "duplicate_event_ignored", event_id=event["event_id"], order_id=event["order_id"])
                return "duplicate"
            db.execute("INSERT INTO processed_events VALUES (?,?)", (event["event_id"], now_iso()))
            outcome = "ignored"
            if event["type"] == "payment.succeeded":
                db.execute("UPDATE orders SET status='paid' WHERE order_id=?", (event["order_id"],))
                _, new = delivery.grant(db, event["order_id"])
                outcome = "delivered" if new else "duplicate"
            elif event["type"] == "refund.succeeded":
                db.execute("UPDATE orders SET status='refunded' WHERE order_id=?", (event["order_id"],))
                delivery.revoke(db, event["order_id"])
                outcome = "revoked"
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    log_action("webhooks", f"event_{outcome}", event_id=event["event_id"], order_id=event["order_id"])
    return outcome
