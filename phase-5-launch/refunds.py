"""Refunds: buyers can ask; only the founder can approve. The system never refunds money on its own.

request() records the buyer's request. approve() refuses unless approved_by == "founder" (the CLI passes that only
after the founder picks "approve"), then asks the MOCKED provider to refund. The provider's signed
refund.succeeded webhook then revokes access.
"""

from __future__ import annotations

import secrets

from store_db import now, one, tx


class RefundError(Exception):
    pass


def request(db, order_id: str, email: str, reason: str) -> str:
    with tx(db):
        order = one(db, "SELECT * FROM orders WHERE id=?", order_id)
        if order is None or order["email"].lower() != email.strip().lower():
            raise RefundError("No paid order with that id and email.")
        if order["status"] != "paid":
            raise RefundError(f"This order is {order['status']}, so it can't be refunded.")
        if one(db, "SELECT 1 FROM refunds WHERE order_id=? AND status IN ('requested','approved')", order_id):
            raise RefundError("A refund request for this order is already open.")
        refund_id = f"re_{secrets.token_hex(8)}"
        db.execute("INSERT INTO refunds (id, order_id, amount_cents, status, reason, requested_at) VALUES (?,?,?,?,?,?)",
                   (refund_id, order_id, order["amount_cents"], "requested", reason[:500], now()))
    return refund_id


def pending(db) -> list:
    return db.execute("SELECT r.*, o.email, o.created_at AS ordered_at FROM refunds r JOIN orders o ON o.id=r.order_id "
                      "WHERE r.status='requested' ORDER BY r.requested_at").fetchall()


def approve(db, provider, refund_id: str, approved_by: str | None):
    """Approve and execute a refund. Returns the provider's signed webhook (payload, signature)."""
    if approved_by != "founder":
        raise PermissionError("Only the founder can approve a refund.")
    with tx(db):
        refund = one(db, "SELECT * FROM refunds WHERE id=?", refund_id)
        if refund is None or refund["status"] != "requested":
            raise RefundError("No open refund request with that id.")
        db.execute("UPDATE refunds SET status='approved', approved_by='founder', decided_at=? WHERE id=?",
                   (now(), refund_id))
        payment = one(db, "SELECT id FROM payments WHERE order_id=?", refund["order_id"])
    return provider.refund(payment["id"], refund["amount_cents"], refund_id)


def deny(db, refund_id: str, approved_by: str | None):
    if approved_by != "founder":
        raise PermissionError("Only the founder can decide on a refund.")
    cur = db.execute("UPDATE refunds SET status='denied', approved_by='founder', decided_at=? "
                     "WHERE id=? AND status='requested'", (now(), refund_id))
    if cur.rowcount != 1:
        raise RefundError("No open refund request with that id.")
