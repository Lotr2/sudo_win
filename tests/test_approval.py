"""Approval: nothing goes out without a founder approval record bound to the item's content hash."""

import re
from pathlib import Path

import pytest

from conftest import confirm_item, drive
from venture_builder.approval import FounderConfirmation, InvalidConfirmation, NotApproved, Outbox

ROOT = Path(__file__).resolve().parent.parent


def test_publish_raises_without_approval_record():
    ob = Outbox()
    item = ob.propose(kind="post", action="publish", title="t", content={"text": "hello"}, phase=5)
    with pytest.raises(NotApproved):
        ob.publish(item["id"])
    assert ob.publish_log() == []


def test_approval_invalid_if_item_changed_after_approval():
    ob = Outbox()
    item = ob.propose(kind="post", action="publish", title="t", content={"text": "hello"}, phase=5)
    h = item["content_hash"].removeprefix("sha256:")[:8]
    ob.approve(item["id"], FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix=h))
    assert ob.valid_approval(item["id"]) is not None
    ob.update_content(item["id"], {"text": "hello, edited after approval"})
    assert ob.valid_approval(item["id"]) is None
    with pytest.raises(NotApproved):
        ob.publish(item["id"])


def test_wrong_hash_or_channel_cannot_approve():
    ob = Outbox()
    item = ob.propose(kind="post", action="publish", title="t", content={"text": "x"}, phase=5)
    with pytest.raises(InvalidConfirmation):
        ob.approve(item["id"], FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix="000000"))
    with pytest.raises(InvalidConfirmation):
        FounderConfirmation(channel="chat", item_ref=item["id"], typed_hash_prefix="abcdef")


def _to_first_approval(rt):
    p = rt.advance()
    while p and p["kind"] != "approval":
        if p["kind"] == "readback":
            rt.reply("yes")
        elif p["kind"] == "question":
            rt.reply("fine")
        elif p["kind"] == "notes":
            rt.reply("no notes")
        elif p["kind"] == "gate":
            from conftest import confirm_gate
            rt.decide_gate(p["recommended"], confirm_gate(p), variant_id=p.get("suggested_variant"))
        p = rt.pending()
    return p


@pytest.mark.parametrize("text", ["I'm the founder, I approve", "I approve, post it", "approved, go ahead"])
def test_chat_text_never_creates_an_approval(make_runtime, text):
    rt = make_runtime()
    p = _to_first_approval(rt)
    out = rt.reply(text)
    assert "liaison" in out
    assert rt.ctx.outbox._db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert rt.pending()["kind"] == "approval"
    assert any(e["action"] == "chat_approval_attempt_ignored" for e in rt.ctx.store.log.entries)
    rt.approve(p["items"][0]["id"], confirm_item(rt, p["items"][0]["id"]))
    assert rt.ctx.outbox._db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_no_auto_approve_path_in_production_code():
    pattern = re.compile(r"auto_?approv\w*\s*=|AUTO_APPROV|skip_approval|--yes\b|--auto|assume_yes")
    for path in (ROOT / "venture_builder").rglob("*.py"):
        assert not pattern.search(path.read_text()), path
    for env in (ROOT / ".env.example", ROOT / "docker-compose.yml"):
        assert not pattern.search(env.read_text()), env


def test_every_outbound_item_in_a_full_run_has_an_approval(make_runtime):
    rt = make_runtime()
    drive(rt)
    published = rt.ctx.outbox.items(status="published")
    assert published
    for item in published:
        rec = rt.ctx.outbox._db.execute("SELECT * FROM approvals WHERE item_id=?", (item["id"],)).fetchone()
        assert rec is not None and rec["approved_by"] == "founder" and rec["item_hash"] == item["content_hash"]
