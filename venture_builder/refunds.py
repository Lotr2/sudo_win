"""Refunds: a refund request becomes an outbox item. Only after the founder approves it does the
executor emit a MOCKED refund event, which revokes access in the same transaction. Without an approval
record nothing happens."""

from __future__ import annotations

from . import webhooks
from .approval import NotApproved, Outbox
from .decision_log import log_action
from .events import EventStore
from .payments import MockPaymentProvider


def request_refund(outbox: Outbox, order_id: str, reason: str, phase: int = 5) -> dict:
    return outbox.propose(kind="refund", action="refund", title=f"Refund order {order_id} (MOCKED)",
                          content={"order_id": order_id, "reason": reason}, phase=phase)


def execute_refund(outbox: Outbox, store: EventStore, item_id: str) -> str:
    """Raises NotApproved (and changes nothing) without a valid founder approval record."""
    item = outbox.get(item_id)
    if outbox.valid_approval(item_id) is None:
        log_action("refunds", "refund_refused", item_id=item_id, order_id=item["content"]["order_id"],
                   reason="no founder approval record")
        raise NotApproved(f"refund {item_id} has no founder approval")
    outbox.publish(item_id, adapter="MOCKED refund")
    event = MockPaymentProvider(store).refund_event(item["content"]["order_id"])
    return webhooks.handle(store, event)
