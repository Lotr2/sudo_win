"""Webhook handler for MOCKED payment events (idempotent consumer with an enforced order state machine).

Each event is processed at most once (keyed by event_id) and each order is delivered at most once
(keyed by order_id). The event, the order status and the delivery change commit in one transaction.

Order states (ARCHITECTURE.md → Sale to cash): pending → paid (delivered) → refunded (access revoked).
A payment is accepted only for a pending order and a refund only for a paid one. Anything else (a refund
for an unpaid order, a payment after a refund, an unknown order) is recorded as processed, changes
nothing, and returns 'rejected'.
"""

from __future__ import annotations

from . import delivery
from .decision_log import log_action, now_iso
from .events import EventStore


def handle(store: EventStore, event: dict) -> str:
    """Returns 'delivered', 'revoked', 'duplicate', 'rejected' or 'ignored'."""
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
            row = db.execute("SELECT status FROM orders WHERE order_id=?", (event["order_id"],)).fetchone()
            status = row[0] if row else None
            outcome, reason = "ignored", None
            if event["type"] == "payment.succeeded":
                if status == "pending":
                    db.execute("UPDATE orders SET status='paid' WHERE order_id=?", (event["order_id"],))
                    _, new = delivery.grant(db, event["order_id"])
                    outcome = "delivered" if new else "duplicate"
                elif status == "paid":
                    outcome = "duplicate"          # a second payment event for a delivered order
                else:
                    outcome, reason = "rejected", f"payment for an order that is {status or 'unknown'}"
            elif event["type"] == "refund.succeeded":
                if status == "paid":
                    db.execute("UPDATE orders SET status='refunded' WHERE order_id=?", (event["order_id"],))
                    delivery.revoke(db, event["order_id"])
                    outcome = "revoked"
                else:
                    outcome, reason = "rejected", f"refund for an order that is {status or 'unknown'}"
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    log_action("webhooks", f"event_{outcome}", event_id=event["event_id"], order_id=event["order_id"], reason=reason)
    return outcome
