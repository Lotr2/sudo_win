"""Reconciliation: match orders, payments, fees, refunds, access grants and payouts, and list every mismatch."""

from __future__ import annotations

from store_db import all_rows


def reconcile(db) -> dict:
    orders = {r["id"]: r for r in all_rows(db, "SELECT * FROM orders")}
    payments = {r["order_id"]: r for r in all_rows(db, "SELECT * FROM payments")}
    grants = {r["order_id"]: r for r in all_rows(db, "SELECT * FROM grants")}
    refunds = all_rows(db, "SELECT * FROM refunds WHERE status='refunded'")
    payouts = all_rows(db, "SELECT * FROM payouts")
    mismatches: list[str] = []

    for oid, o in orders.items():
        pay, grant = payments.get(oid), grants.get(oid)
        if o["status"] in ("paid", "refunded"):
            if pay is None:
                mismatches.append(f"order {oid} is {o['status']} but has no payment")
            elif pay["amount_cents"] != o["amount_cents"]:
                mismatches.append(f"order {oid}: payment {pay['amount_cents']} != order {o['amount_cents']}")
            if grant is None:
                mismatches.append(f"order {oid} was paid but never delivered")
        else:
            if pay is not None:
                mismatches.append(f"order {oid} is {o['status']} but has a payment")
            if grant is not None:
                mismatches.append(f"order {oid} is {o['status']} but has access")
        if o["status"] == "refunded" and grant is not None and grant["revoked_at"] is None:
            mismatches.append(f"order {oid} was refunded but access was not revoked")
        if o["status"] == "paid" and grant is not None and grant["revoked_at"] is not None:
            mismatches.append(f"order {oid} is paid but access was revoked")

    for oid in grants:
        if oid not in orders:
            mismatches.append(f"access grant for unknown order {oid}")

    refunded_by_payment: dict[str, int] = {}
    for r in refunds:
        pay = payments.get(r["order_id"])
        if pay is None:
            mismatches.append(f"refund {r['id']} has no payment")
        else:
            refunded_by_payment[pay["id"]] = refunded_by_payment.get(pay["id"], 0) + r["amount_cents"]
    for pay in payments.values():
        if refunded_by_payment.get(pay["id"], 0) != pay["refunded_cents"]:
            mismatches.append(f"payment {pay['id']}: refunds {refunded_by_payment.get(pay['id'], 0)} != "
                              f"recorded {pay['refunded_cents']}")

    for po in payouts:
        covered = [p for p in payments.values() if p["payout_id"] == po["id"]]
        expected = sum(p["amount_cents"] - p["fee_cents"] - p["refunded_cents"] for p in covered)
        if expected != po["amount_cents"]:
            mismatches.append(f"payout {po['id']}: paid {po['amount_cents']} but payments net {expected}")

    gross = sum(p["amount_cents"] for p in payments.values())
    fees = sum(p["fee_cents"] for p in payments.values())
    refunded = sum(p["refunded_cents"] for p in payments.values())
    paid_out = sum(po["amount_cents"] for po in payouts)
    unsettled = [p["id"] for p in payments.values() if p["payout_id"] is None]
    return {
        "orders": len(orders),
        "by_status": {s: sum(1 for o in orders.values() if o["status"] == s) for s in
                      sorted({o["status"] for o in orders.values()})},
        "gross_cents": gross, "fees_cents": fees, "refunded_cents": refunded,
        "net_cents": gross - fees - refunded, "paid_out_cents": paid_out,
        "balance_cents": gross - fees - refunded - paid_out,
        "unsettled_payments": unsettled, "mismatches": mismatches,
    }
