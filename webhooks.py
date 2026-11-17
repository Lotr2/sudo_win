"""MOCKED webhook handling: signature check, idempotency (each event processed once), payment -> delivery."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

import delivery
from payments import CommerceState

_WEBHOOK_SECRET = secrets.token_bytes(16)   # MOCKED provider signing secret, per process


def sign(event: dict) -> tuple[bytes, str]:
    body = json.dumps(event, sort_keys=True).encode()
    return body, hmac.new(_WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()


def handle(state: CommerceState, body: bytes, signature: str) -> dict:
    """Returns {"status": "delivered"|"duplicate_ignored"|"ignored", ...}. Rejects bad signatures."""
    expected = hmac.new(_WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise PermissionError("invalid webhook signature")
    event = json.loads(body)
    if event["id"] in state.processed_events:
        return {"status": "duplicate_ignored", "event_id": event["id"]}
    state.processed_events.add(event["id"])
    if event["type"] == "payment_succeeded":
        token = delivery.issue_token(state, event["order_id"])
        return {"status": "delivered", "event_id": event["id"], "token": token}
    return {"status": "ignored", "event_id": event["id"]}
