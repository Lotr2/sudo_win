"""Publish/send: write to the local outbox folder only (MOCKED). There is no network posting or email code.

Every write has an idempotency key (idea + action id): the same outward action can never be written twice,
also not after a crash and resume.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from approval import requires_approval
from core.conventions import MOCKED

_HEADER = "<!-- {label}: {kind} '{channel}' at {at}. Written to the local outbox only; nothing was posted or sent. key={key} -->\n"


class DuplicateOutward(RuntimeError):
    """This outward action (key) was already written."""


def key_path(outbox: Path, key: str) -> Path:
    return Path(outbox) / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".txt")


def write_outward(outbox: Path, kind: str, channel: str, content: str, key: str) -> Path:
    path = key_path(outbox, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise DuplicateOutward(key)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(_HEADER.format(label=MOCKED, kind=kind, channel=channel, at=at, key=key) + content, encoding="utf-8")
    return path


@requires_approval("publish")
def publish(outbox: Path, channel: str, *, content: str, key: str) -> Path:
    return write_outward(outbox, "publish", channel, content, key)


@requires_approval("send")
def send(outbox: Path, channel: str, *, content: str, key: str) -> Path:
    return write_outward(outbox, "send", channel, content, key)
