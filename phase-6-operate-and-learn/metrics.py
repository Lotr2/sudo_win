"""Operating metrics, computed by code from the store database, the Phase 2 waitlist and the support tickets.

Only aggregates leave this module: no emails or other personal data. Product usage is NOT measured: the MVP keeps
its data in the user's browser and has no analytics, by design.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if ts else None
    except ValueError:
        return None


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def store_counts(db_path: Path) -> dict:
    empty = {"visitors": 0, "checkouts": 0, "paid": 0, "refunded": 0, "failed": 0, "flagged": 0, "pending": 0,
             "gross_cents": 0, "fees_cents": 0, "refunded_cents": 0, "first_order_at": None, "buyer_emails": set()}
    if not Path(db_path).exists():
        return empty
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        status = dict(db.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
        gross, fees, refunded = db.execute(
            "SELECT COALESCE(SUM(amount_cents),0), COALESCE(SUM(fee_cents),0), COALESCE(SUM(refunded_cents),0) "
            "FROM payments").fetchone()
        has_visits = db.execute("SELECT 1 FROM sqlite_master WHERE name='visits'").fetchone()
        visitors = db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] if has_visits else 0
        first = db.execute("SELECT MIN(created_at) FROM orders").fetchone()[0]
        emails = {r[0].lower() for r in db.execute("SELECT email FROM orders WHERE status IN ('paid','refunded')")}
    finally:
        db.close()
    return {"visitors": visitors, "checkouts": sum(status.values()), "paid": status.get("paid", 0),
            "refunded": status.get("refunded", 0), "failed": status.get("failed", 0),
            "flagged": status.get("amount_mismatch", 0), "pending": status.get("pending", 0),
            "gross_cents": gross, "fees_cents": fees, "refunded_cents": refunded, "first_order_at": first,
            "buyer_emails": emails}


def waitlist_emails(csv_path: Path) -> set[str]:
    if not Path(csv_path).exists():
        return set()
    with open(csv_path, newline="") as f:
        return {row[1].strip().lower() for row in csv.reader(f) if len(row) > 1}


def ticket_counts(tickets_path: Path) -> dict:
    counts: dict[str, int] = {}
    if Path(tickets_path).exists():
        for line in Path(tickets_path).read_text().splitlines():
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            counts[t.get("category", "other")] = counts.get(t.get("category", "other"), 0) + 1
    return counts


def compute(db_path: Path, waitlist_csv: Path, tickets_path: Path, opened_at: str | None, price: dict,
            break_even_customers: int | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = store_counts(db_path)
    start = _parse(opened_at) or _parse(s["first_order_at"]) or now
    ever_paid = s["paid"] + s["refunded"]
    waitlist = waitlist_emails(waitlist_csv)
    tickets = ticket_counts(tickets_path)
    net = s["gross_cents"] - s["fees_cents"] - s["refunded_cents"]
    monthly = {"monthly": 1, "yearly": 1 / 12}.get(price.get("billing"))
    return {
        "days_open": round((now - start).total_seconds() / 86400, 1),
        "store_visitors": s["visitors"],
        "checkouts_started": s["checkouts"],
        "paying_customers": s["paid"],  # paid and not refunded
        "ever_paid": ever_paid,
        "refunded": s["refunded"],
        "failed_payments": s["failed"],
        "flagged_payments": s["flagged"],
        "visit_to_purchase": _rate(ever_paid, s["visitors"]),
        "checkout_completion": _rate(ever_paid, s["checkouts"]),
        "refund_rate": _rate(s["refunded"], ever_paid),
        "gross_cents": s["gross_cents"], "fees_cents": s["fees_cents"], "refunded_cents": s["refunded_cents"],
        "net_cents": net,
        "mrr_cents": round(s["paid"] * price.get("price", 0) * 100 * monthly) if monthly else None,
        "break_even_customers": break_even_customers,
        "waitlist_size": len(waitlist),
        "buyers_from_waitlist": len(waitlist & s["buyer_emails"]),
        "support_tickets": sum(tickets.values()),
        "support_by_category": tickets,
        "product_usage": "not measured (data stays in the user's browser; no analytics by design)",
        "payments": "MOCKED",
    }
