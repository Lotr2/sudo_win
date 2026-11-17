"""Support tickets: store them (git-ignored runtime folder), check drafted replies in code, and summarise without
personal data. Sending is MOCKED: an approved reply is written to the sent folder, not emailed.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from common.guardrails import screen_copy

CATEGORIES = ("question", "bug", "feature_request", "refund_request", "complaint", "other")
EMAIL_RE = re.compile(r"[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
REFUND_PROMISE = re.compile(r"\b(i|we)('ve| have| just)? (refunded|issued (you )?a refund|processed (your|a) refund)|"
                            r"\byou('ll| will) (get|receive|be given) (a|your|the) (full )?refund|\brefund (is|has been) "
                            r"(on its way|approved|processed|issued)", re.I)
FEATURE_PROMISE = re.compile(r"\b(we('ll| will)|i('ll| will)) (add|build|ship|release|launch) (it|this|that)\b|"
                             r"\b(coming|available|ready) (soon|next (week|month))\b|\bby (next|the end of)\b", re.I)
CREDENTIALS = re.compile(r"\b(password|card number|cvv|cvc|security code|login details)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(text: str) -> str:
    return PHONE_RE.sub("[phone]", EMAIL_RE.sub("[email]", text))


def check_reply(reply: str) -> list[str]:
    issues = [f"copy rule: {i}" for i in screen_copy(reply)]
    if REFUND_PROMISE.search(reply):
        issues.append("must not say a refund was or will be made: a person reviews every refund")
    if FEATURE_PROMISE.search(reply):
        issues.append("must not promise features or dates")
    if CREDENTIALS.search(reply) and re.search(r"\b(send|share|give|tell|provide|reply with)\b", reply, re.I):
        issues.append("must never ask for passwords or card details")
    if not reply.strip():
        issues.append("reply is empty")
    return issues


def order_status(db_path: Path, email: str | None) -> list[dict]:
    """Status of this customer's orders (no other customers' data), for grounding the reply."""
    if not email or not Path(db_path).exists():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT o.id, o.status, o.created_at, (SELECT status FROM refunds r WHERE r.order_id=o.id "
                          "ORDER BY requested_at DESC LIMIT 1) FROM orders o WHERE lower(o.email)=? "
                          "ORDER BY o.created_at", (email.strip().lower(),)).fetchall()
    finally:
        db.close()
    return [{"order_id": r[0], "status": r[1], "ordered_at": r[2], "refund_request": r[3]} for r in rows]


class Tickets:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "tickets.jsonl"
        self.sent = self.folder / "sent"

    def add(self, email: str | None, message: str, draft: dict, status: str, reply: str | None) -> dict:
        ticket = {"id": f"t_{secrets.token_hex(6)}", "at": _now(), "email": email, "message": message,
                  "category": draft["category"], "summary": redact(draft["summary"]), "status": status,
                  "feature_request": draft.get("feature_request"), "bug": draft.get("bug")}
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(ticket) + "\n")
        if status == "replied" and reply:
            self.sent.mkdir(parents=True, exist_ok=True)
            (self.sent / f"{ticket['id']}.txt").write_text(f"To: {email or '(no email given)'}\n"
                                                            f"MOCKED: not sent by email\n\n{reply}")
        return ticket

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]


def summary_md(tickets: list[dict]) -> str:
    """Aggregate support summary for outputs/: categories and redacted one-line summaries only."""
    lines = ["# Phase 6, Step 2: support summary", "",
             "Replies were drafted by the support agent and approved by the founder before \"sending\" "
             "(MOCKED: saved, not emailed). Personal details are removed.", "",
             f"**Tickets:** {len(tickets)}", "", "| Category | Count |", "|---|---|"]
    for c in CATEGORIES:
        n = sum(t["category"] == c for t in tickets)
        if n:
            lines.append(f"| {c.replace('_', ' ')} | {n} |")
    lines += ["", "## Tickets", ""]
    lines += [f"- [{t['category']}] {redact(t['summary'])} ({t['status']})" for t in tickets] or ["- (none yet)"]
    feats = [redact(t["feature_request"]) for t in tickets if t.get("feature_request")]
    bugs = [redact(t["bug"]) for t in tickets if t.get("bug")]
    if feats:
        lines += ["", "## Feature requests", ""] + [f"- {x}" for x in feats]
    if bugs:
        lines += ["", "## Bugs reported", ""] + [f"- {x}" for x in bugs]
    return "\n".join(lines) + "\n"
