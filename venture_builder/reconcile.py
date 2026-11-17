"""Reconciliation: matches orders, payments, fees, refunds, deliveries and payouts (all MOCKED) and
reports every mismatch."""

from __future__ import annotations

from .decision_log import log_action
from .events import EventStore


def reconcile(store: EventStore) -> dict:
    r = store.rows
    paid = {x["order_id"]: x for x in r("SELECT * FROM events WHERE type='payment.succeeded'")}
    refund_rows = r("SELECT * FROM events WHERE type='refund.succeeded'")
    refunded = {x["order_id"] for x in refund_rows}
    deliveries = {x["order_id"]: x for x in r("SELECT * FROM deliveries")}
    orders = {x["order_id"]: x for x in r("SELECT * FROM orders")}
    mismatches: list[str] = []
    for oid in paid:
        if oid not in deliveries:
            mismatches.append(f"{oid}: payment with no delivery")
        if oid not in orders:
            mismatches.append(f"{oid}: payment with no order")
        elif abs(orders[oid]["amount"] - paid[oid]["amount"]) > 0.005:
            mismatches.append(f"{oid}: payment amount differs from order amount")
    for oid, d in deliveries.items():
        if oid not in paid:
            mismatches.append(f"{oid}: delivery with no payment")
        if oid in refunded and d["status"] != "revoked":
            mismatches.append(f"{oid}: refunded but access not revoked")
    refund_total: dict[str, float] = {}
    for x in refund_rows:
        refund_total[x["order_id"]] = refund_total.get(x["order_id"], 0) - x["amount"]
    for oid, total in refund_total.items():
        n = sum(1 for x in refund_rows if x["order_id"] == oid)
        if oid not in paid:
            mismatches.append(f"{oid}: refund with no payment")
        elif total - paid[oid]["amount"] > 0.005:
            mismatches.append(f"{oid}: refunded {total:.2f}, more than the {paid[oid]['amount']:.2f} paid")
        if n > 1:
            mismatches.append(f"{oid}: {n} refunds for one order")
    gross = sum(p["amount"] for p in paid.values())
    fees = sum(p["fee"] for p in paid.values())
    refunds = -sum(x["amount"] for x in r("SELECT amount FROM events WHERE type='refund.succeeded'"))
    payouts = r("SELECT COALESCE(SUM(amount),0) AS s FROM payouts")[0]["s"]
    expected_payout = round(gross - fees - refunds, 2)
    if payouts and abs(payouts - expected_payout) > 0.005:
        mismatches.append(f"payouts {payouts:.2f} != gross - fees - refunds {expected_payout:.2f}")
    report = {"orders": len(orders), "payments": len(paid), "gross": round(gross, 2), "fees": round(fees, 2),
              "refunds": round(refunds, 2), "payouts": round(payouts, 2), "expected_payout": expected_payout,
              "deliveries": len(deliveries), "mismatches": mismatches, "label": "MOCKED"}
    log_action("reconcile", "reconcile_books", mismatches=len(mismatches))
    return report
