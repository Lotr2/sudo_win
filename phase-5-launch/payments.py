"""MOCKED payment provider. No real or sandbox payment service is contacted, and no money moves.

It behaves like a real provider from the store's point of view: it creates checkout sessions, takes a fee, sends
HMAC-signed webhook events for payment and refund outcomes, and makes payouts. That lets the real sale-to-cash code
(webhooks, delivery, refunds, reconciliation) run end to end without touching money. Card details are never asked
for, so none can be stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from store_db import all_rows, now, one, tx

MOCKED = "MOCKED"
FEE_PERCENT = 2.9  # MOCKED fee schedule, shaped like common card pricing
FEE_FIXED_CENTS = 30


def fee_for(amount_cents: int) -> int:
    return round(amount_cents * FEE_PERCENT / 100) + FEE_FIXED_CENTS


def load_secret(path: Path) -> str:
    """Webhook signing secret, created once and kept in the git-ignored runtime folder."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


class MockPaymentProvider:
    label = MOCKED

    def __init__(self, db, secret: str):
        self.db = db
        self.secret = secret

    def _event(self, type_: str, data: dict) -> tuple[bytes, str]:
        payload = json.dumps({"id": f"evt_{secrets.token_hex(8)}", "type": type_, "created": now(),
                              "mocked": True, "data": data}, sort_keys=True).encode()
        return payload, sign(payload, self.secret)

    def create_checkout(self, order_id: str, amount_cents: int, currency: str) -> str:
        checkout_id = f"cs_{secrets.token_hex(10)}"
        self.db.execute("INSERT INTO mock_checkouts VALUES (?,?,?,?,?)",
                        (checkout_id, order_id, amount_cents, currency, "open"))
        return checkout_id

    def complete_checkout(self, checkout_id: str, succeed: bool, amount_override: int | None = None):
        """Simulate the buyer finishing (or failing) checkout. Returns the signed webhook (payload, signature)."""
        with tx(self.db):
            row = one(self.db, "SELECT * FROM mock_checkouts WHERE id=?", checkout_id)
            if row is None or row["status"] != "open":
                raise ValueError("checkout not found or already completed")
            self.db.execute("UPDATE mock_checkouts SET status=? WHERE id=?",
                            ("complete" if succeed else "failed", checkout_id))
        amount = row["amount_cents"] if amount_override is None else amount_override
        if not succeed:
            return self._event("payment.failed", {"order_id": row["order_id"], "checkout_id": checkout_id})
        return self._event("payment.succeeded", {
            "order_id": row["order_id"], "checkout_id": checkout_id, "payment_id": f"pay_{secrets.token_hex(8)}",
            "amount_cents": amount, "fee_cents": fee_for(amount), "currency": row["currency"]})

    def refund(self, payment_id: str, amount_cents: int, refund_id: str):
        """Simulate the provider refunding a payment. Only refunds.approve() calls this, after founder approval."""
        pay = one(self.db, "SELECT order_id FROM payments WHERE id=?", payment_id)
        return self._event("refund.succeeded", {"payment_id": payment_id, "order_id": pay["order_id"],
                                                "refund_id": refund_id, "amount_cents": amount_cents})

    def create_payout(self) -> dict | None:
        """Pay out every settled payment not yet paid out: amount - fee - refunds."""
        with tx(self.db):
            rows = all_rows(self.db, "SELECT * FROM payments WHERE payout_id IS NULL")
            if not rows:
                return None
            payout_id = f"po_{secrets.token_hex(8)}"
            amount = sum(r["amount_cents"] - r["fee_cents"] - r["refunded_cents"] for r in rows)
            self.db.execute("INSERT INTO payouts VALUES (?,?,?)", (payout_id, amount, now()))
            self.db.execute("UPDATE payments SET payout_id=? WHERE payout_id IS NULL", (payout_id,))
        return {"id": payout_id, "amount_cents": amount, "payments": [r["id"] for r in rows]}
