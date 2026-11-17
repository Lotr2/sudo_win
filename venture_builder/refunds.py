"""Refunds: a refund request becomes an outbox item. Only after the founder approves it does the
executor emit a MOCKED refund event, which revokes access in the same transaction. Without an approval
record nothing happens. An approval is spent by the first execution, and the refund event's id is fixed by
the outbox item, so one approval can never refund twice. Only a paid order can be refunded."""

from __future__ import annotations

from . import webhooks
from .approval import AlreadyExecuted, NotApproved, Outbox
from .decision_log import log_action
from .events import EventStore
from .payments import MockPaymentProvider


def request_refund(outbox: Outbox, order_id: str, reason: str, phase: int = 5) -> dict:
    return outbox.propose(kind="refund", action="refund", title=f"Refund order {order_id} (MOCKED)",
                          content={"order_id": order_id, "reason": reason}, phase=phase)


def execute_refund(outbox: Outbox, store: EventStore, item_id: str) -> str:
    """Raises NotApproved (and changes nothing) without a valid founder approval record, AlreadyExecuted if
    this approval was already used, and RefundNotAllowed if the order isn't paid."""
    item = outbox.get(item_id)
    order_id = item["content"]["order_id"]
    if item["status"] == "published":
        log_action("refunds", "refund_refused", item_id=item_id, order_id=order_id, reason="approval already used")
        raise AlreadyExecuted(f"refund {item_id} was already executed")
    if outbox.valid_approval(item_id) is None:
        log_action("refunds", "refund_refused", item_id=item_id, order_id=order_id,
                   reason="no founder approval record")
        raise NotApproved(f"refund {item_id} has no founder approval")
    order = store.rows("SELECT status FROM orders WHERE order_id=?", (order_id,))
    if not order or order[0]["status"] != "paid":
        state = order[0]["status"] if order else "unknown"
        log_action("refunds", "refund_refused", item_id=item_id, order_id=order_id, reason=f"order is {state}")
        raise RefundNotAllowed(f"order {order_id} is {state}; only a paid order can be refunded")
    outbox.publish(item_id, adapter="MOCKED refund")     # spends the approval (raises if already used)
    event = MockPaymentProvider(store).refund_event(order_id, refund_id=item_id)
    return webhooks.handle(store, event)


class RefundNotAllowed(ValueError):
    """The order is not in a state that can be refunded (not paid, already refunded, or unknown)."""
