"""MOCKED payment provider. No payments of any kind: no real or sandbox provider, no card data, no accounts.

It produces payment events shaped like a real provider's (order, fee, payout) so that delivery,
refunds, idempotency and reconciliation can be exercised end to end.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from approval import payload_hash, requires_approval
from core.conventions import MOCKED

MOCK_FEE_PCT = 0.029     # MOCKED fee schedule
MOCK_FEE_FIXED = 0.30


@dataclass
class CommerceState:
    """All commerce records for one run (persisted next to the run, never in outputs/)."""
    listing: dict | None = None
    orders: dict = field(default_factory=dict)        # order_id -> order
    events: list = field(default_factory=list)        # provider events (MOCKED)
    processed_events: set = field(default_factory=set)
    tokens: dict = field(default_factory=dict)        # token -> {order_id, revoked}
    deliveries: list = field(default_factory=list)
    refunds: list = field(default_factory=list)
    payouts: list = field(default_factory=list)

    def save(self, path: Path) -> None:
        d = asdict(self)
        d["processed_events"] = sorted(self.processed_events)
        Path(path).write_text(json.dumps(d, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CommerceState":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["processed_events"] = set(d.get("processed_events", []))
        return cls(**d)


@requires_approval("open_store")
def open_store(state: CommerceState, product: str, price_usd: float, *, content: str) -> dict:
    """Create the MOCKED store listing (founder-approved)."""
    state.listing = {"product": product, "price_usd": round(float(price_usd), 2), "label": MOCKED,
                     "page_sha256": payload_hash(content)}
    return state.listing


def fee_for(amount: float) -> float:
    return round(amount * MOCK_FEE_PCT + MOCK_FEE_FIXED, 2)


def simulate_test_order(state: CommerceState, order_id: str) -> dict:
    """A MOCKED buyer completes a MOCKED checkout. Returns the provider's payment_succeeded event."""
    if not state.listing:
        raise RuntimeError("store is not open (no founder-approved listing)")
    if order_id in state.orders:      # idempotent: a resumed run never creates the same test order twice
        return next(e for e in state.events if e["id"] == f"evt_{order_id}_paid")
    amount = state.listing["price_usd"]
    state.orders[order_id] = {"order_id": order_id, "amount": amount, "status": "paid", "label": MOCKED}
    event = {"id": f"evt_{order_id}_paid", "type": "payment_succeeded", "order_id": order_id,
             "amount": amount, "fee": fee_for(amount), "label": MOCKED}
    state.events.append(event)
    return event


def mock_payout(state: CommerceState) -> dict | None:
    """MOCKED payout of net funds not yet paid out (idempotent: nothing is paid out twice)."""
    gross = sum(e["amount"] for e in state.events if e["type"] == "payment_succeeded")
    fees = sum(e["fee"] for e in state.events if e["type"] == "payment_succeeded")
    refunded = sum(r["amount"] for r in state.refunds)
    due = round(gross - fees - refunded - sum(p["amount"] for p in state.payouts), 2)
    if abs(due) < 0.005:
        return None
    payout = {"id": f"po_{len(state.payouts) + 1}", "amount": due, "label": MOCKED}
    state.payouts.append(payout)
    return payout
