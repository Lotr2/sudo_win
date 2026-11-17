"""Automatic delivery (MOCKED): a paid order gets an access token; a refund revokes it. No buyer accounts."""
from __future__ import annotations

import secrets

from payments import CommerceState


def issue_token(state: CommerceState, order_id: str) -> str:
    existing = [t for t, v in state.tokens.items() if v["order_id"] == order_id]
    if existing:                      # never deliver twice for one order
        return existing[0]
    token = secrets.token_urlsafe(16)
    state.tokens[token] = {"order_id": order_id, "revoked": False}
    state.deliveries.append({"order_id": order_id, "token_prefix": token[:6]})
    return token


def tokens_for(state: CommerceState, order_id: str) -> list[str]:
    return [t for t, v in state.tokens.items() if v["order_id"] == order_id]


def can_download(state: CommerceState, token: str) -> bool:
    t = state.tokens.get(token)
    return bool(t) and not t["revoked"]


def revoke_for_order(state: CommerceState, order_id: str) -> int:
    n = 0
    for v in state.tokens.values():
        if v["order_id"] == order_id and not v["revoked"]:
            v["revoked"] = True
            n += 1
    return n
