"""MOCKED payment provider. No SDK, no keys, no card data: it only emits mock events into the event log.
Fees follow a mock schedule (2.9% + $0.30) so reconciliation has fees and payouts to match."""

from __future__ import annotations

from .decision_log import log_action, now_iso
from .events import EventStore

LABEL = "MOCKED"


def mock_fee(amount: float) -> float:
    return round(amount * 0.029 + 0.30, 2)


class MockPaymentProvider:
    def __init__(self, store: EventStore):
        self.store = store

    def create_checkout(self, product: str, amount: float) -> str:
        with self.store.transaction():
            order_id = self.store.next_id("orders", "ord_", 3)
            self.store.db.execute("INSERT INTO orders VALUES (?,?,?,?,?)", (order_id, product, amount, "pending", now_iso()))
        self.store.append("checkout.created", order_id, amount)
        log_action("payments", "mock_checkout_created", order_id=order_id, amount=amount, label=LABEL)
        return order_id

    def succeed(self, order_id: str) -> dict:
        """Emit a mock payment.succeeded event. Delivery happens only when the webhook handles it."""
        amount = self.store.rows("SELECT amount FROM orders WHERE order_id=?", (order_id,))[0]["amount"]
        ev = self.store.append("payment.succeeded", order_id, amount, mock_fee(amount))
        log_action("payments", "mock_payment_succeeded", order_id=order_id, event_id=ev["event_id"], label=LABEL)
        return ev

    def refund_event(self, order_id: str, refund_id: str) -> dict:
        """Emit a mock refund.succeeded event. `refund_id` (the outbox item) fixes the event id, so emitting the
        same refund again returns the first event instead of creating a second one."""
        amount = self.store.rows("SELECT amount FROM orders WHERE order_id=?", (order_id,))[0]["amount"]
        return self.store.append("refund.succeeded", order_id, -amount, event_id=f"evt_refund_{refund_id}")

    def payout(self) -> dict:
        """Mock payout of everything paid, net of fees and refunds."""
        net = self.store.rows(
            "SELECT COALESCE(SUM(amount - fee), 0) AS net FROM events WHERE type IN ('payment.succeeded','refund.succeeded')")[0]["net"]
        paid_out = self.store.rows("SELECT COALESCE(SUM(amount),0) AS s FROM payouts")[0]["s"]
        amount = round(net - paid_out, 2)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO payouts VALUES (?,?,?)",
                                  (self.store.next_id("payouts", "po_", 3), amount, now_iso()))
        ev = self.store.append("payout.paid", None, amount)
        log_action("payments", "mock_payout", amount=amount, label=LABEL)
        return ev
