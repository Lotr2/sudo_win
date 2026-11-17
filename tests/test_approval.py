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


def test_rejected_item_note_reaches_the_writer(make_runtime, models):
    seen = []
    orig = models.landing_copywriter
    models.landing_copywriter = lambda p: (seen.append(p.get("feedback", "")), orig(p))[1]
    rt = make_runtime()
    p = _to_first_approval(rt)
    rt.approve(p["items"][0]["id"], confirm_item(rt, p["items"][0]["id"]))  # experiment targets
    p = rt.pending()
    assert p["kind"] == "approval" and p["items"][0]["kind"] == "landing_page"
    rt.reject(p["items"][0]["id"], "Say who it is for in the headline", "cli")
    assert seen[-1] == "Say who it is for in the headline"
    assert rt.pending()["kind"] == "approval"


def test_simulation_channel_is_refused_unless_explicitly_enabled(monkeypatch):
    import pytest
    from venture_builder.approval import FounderConfirmation, InvalidConfirmation
    monkeypatch.delenv("VENTURE_SIMULATED_FOUNDER", raising=False)
    with pytest.raises(InvalidConfirmation):
        FounderConfirmation(channel="simulation", item_ref="x", typed_hash_prefix="abcdef")
    monkeypatch.setenv("VENTURE_SIMULATED_FOUNDER", "1")
    FounderConfirmation(channel="simulation", item_ref="x", typed_hash_prefix="abcdef")


def test_web_ui_never_starts_with_a_simulated_founder(settings, monkeypatch):
    import pytest
    from venture_builder.ui.server import create_app
    monkeypatch.setenv("VENTURE_SIMULATED_FOUNDER", "1")
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_simulated_founder_decisions_are_labelled_in_the_log(make_runtime, monkeypatch):
    import json
    from conftest import drive
    from venture_builder.approval import FounderConfirmation, gate_token
    monkeypatch.setenv("VENTURE_SIMULATED_FOUNDER", "1")
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    rt.decide_gate(p["recommended"], FounderConfirmation(channel="simulation", item_ref=f"gate1-{p['attempt']}",
                   typed_hash_prefix=gate_token(1, p["attempt"])[:8]), variant_id=p.get("suggested_variant"))
    log = json.loads(rt.ctx.store.path("decision_log.json").read_text())
    decision = [e for e in log if e["action"] == "gate1_decision"][-1]
    assert decision["approved_by"] == "founder" and decision["simulated_founder"] is True
    assert decision["channel"] == "simulation"
