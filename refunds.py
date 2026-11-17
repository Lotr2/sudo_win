"""Refunds: the system only PROPOSES; the founder approves via approval.py; then the MOCKED refund runs and access is revoked."""
from __future__ import annotations

import delivery
from approval import requires_approval
from core.conventions import MOCKED
from payments import CommerceState


def propose(state: CommerceState, order_id: str, reason: str) -> dict:
    order = state.orders[order_id]
    return {"order_id": order_id, "amount": order["amount"], "reason": reason,
            "content": f"Refund {order['amount']:.2f} USD for order {order_id} ({reason})"}


@requires_approval("refund")
def execute(state: CommerceState, order_id: str, *, content: str) -> dict:
    order = state.orders[order_id]
    if order["status"] == "refunded":
        return {"status": "already_refunded", "order_id": order_id}
    order["status"] = "refunded"
    refund = {"order_id": order_id, "amount": order["amount"], "label": MOCKED}
    state.refunds.append(refund)
    state.events.append({"id": f"evt_{order_id}_refund", "type": "refund_succeeded", "order_id": order_id,
                         "amount": order["amount"], "fee": 0.0, "label": MOCKED})
    revoked = delivery.revoke_for_order(state, order_id)
    return {"status": "refunded", "order_id": order_id, "revoked_tokens": revoked}
