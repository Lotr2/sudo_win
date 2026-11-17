"""Delivery: a successful payment grants access to the product exactly once; a refund revokes it.

Access is a secret link (`/app/<token>/`) that the store server checks on every request, so revoking the grant
cuts access immediately. The delivery email is MOCKED: it goes to the outbox table, not to a mail server.

Delivery is automatic, under a standing rule the founder approves once in `prepare` (the email template and
"send it when a payment succeeds"). Without that approval, nothing is delivered.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from store_db import now, one

EMAIL_TEMPLATE = """Hi,

Thanks for buying {product}. Your payment of {price} went through.

Open {product} here (keep this link private, it is your access):
{link}

{refund_note}
Questions: reply to this email or write to {contact_email}.

{business_name}
"""


class DeliveryNotApproved(Exception):
    pass


def template_sha(template: str) -> str:
    return hashlib.sha256(template.encode()).hexdigest()


def approve_rule(path: Path, facts: dict) -> dict:
    """Record the founder's standing approval of the delivery email and rule. Call only after explicit approval."""
    rule = {"template_sha256": template_sha(EMAIL_TEMPLATE), "facts": facts, "approved_by": "founder",
            "approved_at": now(), "rule": "When a payment succeeds, grant access and send the delivery email."}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rule, indent=2) + "\n")
    return rule


def load_rule(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise DeliveryNotApproved("The founder has not approved the delivery rule. Run `prepare` first.")
    rule = json.loads(path.read_text())
    if rule.get("approved_by") != "founder" or rule.get("template_sha256") != template_sha(EMAIL_TEMPLATE):
        raise DeliveryNotApproved("The delivery email changed since the founder approved it. Run `prepare` again.")
    return rule


def render_email(rule: dict, link: str) -> str:
    return EMAIL_TEMPLATE.format(link=link, **rule["facts"])


def deliver(db, order, rule: dict, base_url: str) -> tuple[str, bool]:
    """Grant access for a paid order. Idempotent: returns (token, created) and never creates a second grant.

    Must be called inside the webhook's transaction.
    """
    token = secrets.token_urlsafe(24)
    cur = db.execute("INSERT OR IGNORE INTO grants (order_id, token, created_at) VALUES (?,?,?)",
                     (order["id"], token, now()))
    if cur.rowcount == 0:
        return one(db, "SELECT token FROM grants WHERE order_id=?", order["id"])["token"], False
    link = f"{base_url.rstrip('/')}/app/{token}/"
    db.execute("INSERT INTO outbox (order_id, kind, to_email, subject, body, created_at) VALUES (?,?,?,?,?,?)",
               (order["id"], "delivery", order["email"], f"Your access to {rule['facts']['product']} (MOCKED email)",
                render_email(rule, link), now()))
    return token, True


def revoke(db, order_id: str) -> bool:
    cur = db.execute("UPDATE grants SET revoked_at=? WHERE order_id=? AND revoked_at IS NULL", (now(), order_id))
    return cur.rowcount == 1


def access_ok(db, token: str) -> bool:
    row = one(db, "SELECT g.revoked_at, o.status FROM grants g JOIN orders o ON o.id = g.order_id WHERE g.token=?",
              token)
    return bool(row) and row["revoked_at"] is None and row["status"] == "paid"
