"""Reconciliation: orders <-> payments <-> fees <-> refunds <-> payouts (all MOCKED)."""
from __future__ import annotations

import csv
import io

from payments import CommerceState


def reconcile(state: CommerceState) -> tuple[list[dict], bool]:
    paid = {e["order_id"]: e for e in state.events if e["type"] == "payment_succeeded"}
    refunded = {r["order_id"]: r for r in state.refunds}
    rows = []
    for oid, order in state.orders.items():
        p = paid.get(oid)
        rows.append({"order_id": oid, "order_amount": order["amount"], "payment_amount": p["amount"] if p else 0,
                     "fee": p["fee"] if p else 0, "refund": refunded[oid]["amount"] if oid in refunded else 0,
                     "status": order["status"], "matched": bool(p) and abs(p["amount"] - order["amount"]) < 0.005,
                     "label": "MOCKED"})
    net = round(sum(r["payment_amount"] - r["fee"] - r["refund"] for r in rows), 2)
    payout_total = round(sum(p["amount"] for p in state.payouts), 2)
    balanced = all(r["matched"] for r in rows) and abs(net - payout_total) < 0.005
    rows.append({"order_id": "TOTAL", "order_amount": round(sum(r["order_amount"] for r in rows), 2),
                 "payment_amount": round(sum(r["payment_amount"] for r in rows), 2),
                 "fee": round(sum(r["fee"] for r in rows), 2), "refund": round(sum(r["refund"] for r in rows), 2),
                 "status": f"net={net} payouts={payout_total}", "matched": balanced, "label": "MOCKED"})
    return rows, balanced


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
