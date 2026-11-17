"""Metrics Engine [code]: computes Phase 6 numbers from the (MOCKED) event log only, so they cannot be
invented. The memo writer may only cite these keys."""

from __future__ import annotations

from .decision_log import log_action
from .events import EventStore


def compute(store: EventStore, label: str = "MOCKED") -> dict:
    """label: "MOCKED" for practice numbers, "founder-reported" for real numbers the founder entered."""
    act = store.rows("SELECT kind, SUM(value) AS v, COUNT(*) AS n FROM activity GROUP BY kind")
    by = {a["kind"]: a for a in act}
    visitors = int(by.get("visitors", {}).get("v") or 0)
    orders = int(by.get("orders", {}).get("v") or 0)
    refunds = int(by.get("refunds", {}).get("v") or 0)
    tickets = int(by.get("ticket", {}).get("n") or 0)
    m = {
        "visitors": visitors,
        "orders": orders,
        "refunds": refunds,
        "support_tickets": tickets,
        "conversion": round(orders / visitors, 4) if visitors else 0.0,
        "refund_rate": round(refunds / orders, 4) if orders else 0.0,
        "tickets_per_order": round(tickets / orders, 4) if orders else 0.0,
        "label": label,
    }
    log_action("metrics_engine", "compute_metrics", **{k: v for k, v in m.items() if k != "label"})
    return m
