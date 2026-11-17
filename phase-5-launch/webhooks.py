"""Payment webhooks: verify the signature, process each event exactly once, and drive the order state machine.

  payment.succeeded  -> order paid, payment and fee recorded, access delivered (once)
  payment.failed     -> order failed, nothing delivered
  refund.succeeded   -> refund recorded, order refunded, access revoked

Idempotency: the event id is inserted first, in the same transaction as its effects. A re-delivered event hits the
PRIMARY KEY and is ignored, so a retry can never deliver twice or refund twice. Any error rolls the whole event back,
so the provider's retry gets a clean second attempt.
"""

from __future__ import annotations

import hmac
import json

import delivery
from payments import sign
from store_db import now, one, tx


class InvalidSignature(Exception):
    pass


def verify(payload: bytes, signature: str, secret: str):
    if not signature or not hmac.compare_digest(sign(payload, secret), signature):
        raise InvalidSignature("webhook signature does not match")


def handle(db, payload: bytes, signature: str, secret: str, rule: dict, base_url: str) -> str:
    """Process one webhook. Returns 'processed', 'duplicate' or 'ignored:<reason>'."""
    verify(payload, signature, secret)
    event = json.loads(payload)
    data = event.get("data") or {}
    with tx(db):
        cur = db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?)",
                         (event["id"], event["type"], data.get("order_id"), now()))
        if cur.rowcount == 0:
            return "duplicate"
        order = one(db, "SELECT * FROM orders WHERE id=?", data.get("order_id"))
        if order is None:
            return "ignored:unknown order"

        if event["type"] == "payment.succeeded":
            if order["status"] != "pending":
                return f"ignored:order already {order['status']}"
            if data["amount_cents"] != order["amount_cents"] or data["currency"] != order["currency"]:
                db.execute("UPDATE orders SET status='amount_mismatch', note=? WHERE id=?",
                           (f"paid {data['amount_cents']} {data['currency']}, expected {order['amount_cents']} "
                            f"{order['currency']}", order["id"]))
                return "ignored:amount mismatch, not delivered"
            db.execute("INSERT INTO payments (id, order_id, amount_cents, fee_cents, created_at) VALUES (?,?,?,?,?)",
                       (data["payment_id"], order["id"], data["amount_cents"], data["fee_cents"], now()))
            db.execute("UPDATE orders SET status='paid' WHERE id=?", (order["id"],))
            delivery.deliver(db, {**dict(order), "status": "paid"}, rule, base_url)
            return "processed"

        if event["type"] == "payment.failed":
            if order["status"] == "pending":
                db.execute("UPDATE orders SET status='failed' WHERE id=?", (order["id"],))
            return "processed"

        if event["type"] == "refund.succeeded":
            refund = one(db, "SELECT * FROM refunds WHERE id=?", data.get("refund_id"))
            if refund is None or refund["status"] != "approved" or refund["approved_by"] != "founder":
                return "ignored:refund was not approved by the founder"
            db.execute("UPDATE refunds SET status='refunded' WHERE id=?", (refund["id"],))
            db.execute("UPDATE payments SET refunded_cents = refunded_cents + ? WHERE id=?",
                       (data["amount_cents"], data["payment_id"]))
            db.execute("UPDATE orders SET status='refunded' WHERE id=?", (order["id"],))
            delivery.revoke(db, order["id"])
            return "processed"

        return f"ignored:unknown event type {event['type']}"
