"""Publish/send: write to the local outbox folder only (MOCKED). There is no network posting or email code."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from approval import requires_approval
from core.conventions import MOCKED

_HEADER = "<!-- {label}: {kind} '{channel}' was written to the local outbox only. Nothing was posted or sent. -->\n"


def _write(outbox: Path, kind: str, channel: str, content: str) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = outbox / f"{stamp}_{kind}_{channel}.txt"
    path.write_text(_HEADER.format(label=MOCKED, kind=kind, channel=channel) + content, encoding="utf-8")
    return path


@requires_approval("publish")
def publish(outbox: Path, channel: str, *, content: str) -> Path:
    return _write(Path(outbox), "publish", channel, content)


@requires_approval("send")
def send(outbox: Path, channel: str, *, content: str) -> Path:
    return _write(Path(outbox), "send", channel, content)
