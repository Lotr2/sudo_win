"""Payment and delivery test run (MOCKED payments). Exercises the real sale-to-cash code end to end on a throwaway
database, including product access over real HTTP, and records each scenario's expected vs. actual result.

The results feed the launch gate and are written to outputs/payment_test_log.md.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import delivery
import refunds
import webhooks
from checkout import start_checkout
from payments import MockPaymentProvider, fee_for
from reconcile import reconcile
from server import Store, make_server
from store_db import connect, one


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


def run_payment_test(workdir: Path, store_dir: Path, product_dir: Path, facts: dict, rule: dict,
                     policy_files: dict) -> dict:
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    db = connect(workdir / "test_run.db")
    secret = secrets.token_hex(32)
    provider = MockPaymentProvider(db, secret)
    store = Store(db, provider, secret, rule, facts, store_dir, product_dir, policy_files, "http://127.0.0.1")
    server = make_server(store, "127.0.0.1", 0)
    store.base_url = base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    amount, currency = facts["amount_cents"], facts["currency"]
    results: list[dict] = []

    def check(name: str, expected: str, actual: str, passed: bool):
        results.append({"scenario": name, "expected": expected, "actual": actual, "passed": bool(passed)})

    def hook(payload, sig):
        return webhooks.handle(db, payload, sig, secret, rule, base)

    def count(sql, *a):
        return one(db, sql, *a)[0]

    try:
        # 1. Successful payment triggers delivery
        o1, c1 = start_checkout(db, provider, "buyer1@example.com", amount, currency)
        p1, s1 = provider.complete_checkout(c1, succeed=True)
        r = hook(p1, s1)
        token1 = one(db, "SELECT token FROM grants WHERE order_id=?", o1)["token"]
        http = _status(f"{base}/app/{token1}/")
        check("Payment triggers delivery", "order paid, 1 access grant, 1 delivery email, product opens (HTTP 200)",
              f"{r}; status={one(db, 'SELECT status FROM orders WHERE id=?', o1)['status']}, "
              f"grants={count('SELECT COUNT(*) FROM grants WHERE order_id=?', o1)}, "
              f"emails={count('SELECT COUNT(*) FROM outbox WHERE order_id=?', o1)}, HTTP {http}",
              r == "processed" and count("SELECT COUNT(*) FROM grants WHERE order_id=?", o1) == 1
              and count("SELECT COUNT(*) FROM outbox WHERE order_id=?", o1) == 1 and http == 200)

        # 2. Re-delivered webhook is ignored (idempotency)
        r = hook(p1, s1)
        check("Duplicate webhook does not deliver twice", "duplicate; still 1 grant and 1 email",
              f"{r}; grants={count('SELECT COUNT(*) FROM grants WHERE order_id=?', o1)}, "
              f"emails={count('SELECT COUNT(*) FROM outbox WHERE order_id=?', o1)}",
              r == "duplicate" and count("SELECT COUNT(*) FROM grants WHERE order_id=?", o1) == 1
              and count("SELECT COUNT(*) FROM outbox WHERE order_id=?", o1) == 1)

        # 3. Forged webhook is rejected
        o3, c3 = start_checkout(db, provider, "buyer3@example.com", amount, currency)
        forged = json.dumps({"id": "evt_forged", "type": "payment.succeeded", "data": {
            "order_id": o3, "payment_id": "pay_forged", "amount_cents": amount, "fee_cents": 0,
            "currency": currency}}).encode()
        try:
            hook(forged, "0" * 64)
            actual, ok = "accepted", False
        except webhooks.InvalidSignature:
            actual, ok = "rejected: invalid signature", True
        status3 = one(db, "SELECT status FROM orders WHERE id=?", o3)["status"]
        check("Forged webhook is rejected", "rejected; order stays pending, no access",
              f"{actual}; order {status3}", ok and status3 == "pending"
              and count("SELECT COUNT(*) FROM grants WHERE order_id=?", o3) == 0)

        # 4. Failed payment delivers nothing
        p4, s4 = provider.complete_checkout(c3, succeed=False)
        hook(p4, s4)
        status4 = one(db, "SELECT status FROM orders WHERE id=?", o3)["status"]
        check("Failed payment delivers nothing", "order failed, no access grant, no email",
              f"order {status4}, grants={count('SELECT COUNT(*) FROM grants WHERE order_id=?', o3)}",
              status4 == "failed" and count("SELECT COUNT(*) FROM grants WHERE order_id=?", o3) == 0
              and count("SELECT COUNT(*) FROM outbox WHERE order_id=?", o3) == 0)

        # 5. Wrong amount is not delivered
        o5, c5 = start_checkout(db, provider, "buyer5@example.com", amount, currency)
        p5, s5 = provider.complete_checkout(c5, succeed=True, amount_override=amount // 2)
        r = hook(p5, s5)
        status5 = one(db, "SELECT status FROM orders WHERE id=?", o5)["status"]
        check("Payment for the wrong amount is not delivered", "order flagged, no access",
              f"{r}; order {status5}", status5 == "amount_mismatch"
              and count("SELECT COUNT(*) FROM grants WHERE order_id=?", o5) == 0)

        # 6. Refunds need the founder
        refund_id = refunds.request(db, o1, "buyer1@example.com", "Not what I needed")
        try:
            refunds.approve(db, provider, refund_id, approved_by=None)
            actual, ok = "refund executed", False
        except PermissionError:
            actual, ok = "refused: only the founder can approve", True
        check("Refund without founder approval is refused", "refused; buyer keeps access until a decision",
              f"{actual}; access {'ok' if delivery.access_ok(db, token1) else 'lost'}",
              ok and delivery.access_ok(db, token1)
              and one(db, "SELECT status FROM refunds WHERE id=?", refund_id)["status"] == "requested")

        # 7. Founder-approved refund revokes access
        rp, rs = refunds.approve(db, provider, refund_id, approved_by="founder")
        r = hook(rp, rs)
        http = _status(f"{base}/app/{token1}/")
        status7 = one(db, "SELECT status FROM orders WHERE id=?", o1)["status"]
        check("Approved refund revokes access", "order refunded, access link stops working (HTTP 403)",
              f"{r}; order {status7}, HTTP {http}", r == "processed" and status7 == "refunded" and http == 403)

        # 8. Payout and reconciliation
        o8, c8 = start_checkout(db, provider, "buyer8@example.com", amount, currency)
        hook(*provider.complete_checkout(c8, succeed=True))
        payout = provider.create_payout()
        expected_payout = (amount - fee_for(amount)) + (amount - fee_for(amount) - amount)
        rec = reconcile(db)
        check("Payout matches payments minus fees and refunds",
              f"payout {expected_payout} cents, 0 mismatches",
              f"payout {payout['amount_cents'] if payout else None} cents, {len(rec['mismatches'])} mismatches",
              payout is not None and payout["amount_cents"] == expected_payout and not rec["mismatches"])

        # 9. Reconciliation catches a broken record (on a copy, so the real run stays clean)
        copy_path = workdir / "tampered.db"
        src = sqlite3.connect(str(workdir / "test_run.db"))
        dst = sqlite3.connect(str(copy_path))
        src.backup(dst)
        src.close()
        dst.execute("DELETE FROM grants WHERE order_id=?", (o8,))
        dst.execute("UPDATE payouts SET amount_cents = amount_cents + 100")
        dst.commit()
        dst.close()
        bad_db = connect(copy_path)
        bad = reconcile(bad_db)
        bad_db.close()
        caught = [m for m in bad["mismatches"] if "never delivered" in m or "payout" in m]
        check("Reconciliation catches mismatches", "reports the missing delivery and the wrong payout",
              f"{len(bad['mismatches'])} mismatches: " + "; ".join(bad["mismatches"])[:200], len(caught) >= 2)
    finally:
        server.shutdown()
        server.server_close()
        db.close()

    return {"passed": all(r["passed"] for r in results), "results": results, "reconciliation": rec,
            "amount_cents": amount, "currency": currency, "mocked": True}


def render_log(run: dict, when: str) -> str:
    lines = ["# Payment and delivery test log", "",
             "**Payments are MOCKED.** No real or sandbox payment provider was contacted and no money moved. A "
             "mock provider sent HMAC-signed webhooks into the real store code (webhooks, delivery, refunds, "
             "reconciliation) on a throwaway database. Product access was checked over real HTTP.", "",
             f"**Run at:** {when}  ", f"**Result:** {'ALL PASSED' if run['passed'] else 'FAILED'}  ",
             f"**Test price:** {run['amount_cents']} cents {run['currency']}", "",
             "| # | Scenario | Expected | Actual | Result |", "|---|---|---|---|---|"]
    for i, r in enumerate(run["results"], 1):
        lines.append(f"| {i} | {r['scenario']} | {r['expected']} | {r['actual']} | {'PASS' if r['passed'] else 'FAIL'} |")
    rec = run["reconciliation"]
    lines += ["", "## Reconciliation (after scenario 8)", "",
              f"- Orders: {rec['orders']} ({', '.join(f'{k}: {v}' for k, v in rec['by_status'].items())})",
              f"- Gross {rec['gross_cents']}, fees {rec['fees_cents']} (MOCKED fee schedule), refunded "
              f"{rec['refunded_cents']}, net {rec['net_cents']}, paid out {rec['paid_out_cents']}, balance "
              f"{rec['balance_cents']} (cents)", f"- Mismatches: {len(rec['mismatches'])}"]
    return "\n".join(lines) + "\n"
