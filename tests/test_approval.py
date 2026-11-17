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
    rt.reject(p["items"][0]["id"], "Say who it is for in the headline", confirm_item(rt, p["items"][0]["id"]))
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
    from venture_builder.approval import FounderConfirmation
    monkeypatch.setenv("VENTURE_SIMULATED_FOUNDER", "1")
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    rt.decide_gate(p["recommended"], FounderConfirmation(channel="simulation", item_ref=f"gate1-{p['attempt']}",
                   typed_hash_prefix=p["code"]), variant_id=p.get("suggested_variant"))
    log = rt.ctx.store.log.entries
    decision = [e for e in log if e["action"] == "gate1_decision"][-1]
    assert decision["approved_by"] == "founder" and decision["simulated_founder"] is True
    assert decision["channel"] == "simulation"


# ---- approvals are single use; gate codes are random and one-time; rejections need a confirmation ----

def test_an_approval_publishes_only_once():
    from venture_builder.approval import AlreadyExecuted
    ob = Outbox()
    item = ob.propose(kind="post", action="publish", title="t", content={"text": "hi"}, phase=5)
    h = item["content_hash"].removeprefix("sha256:")[:8]
    ob.approve(item["id"], FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix=h))
    ob.publish(item["id"])
    with pytest.raises(AlreadyExecuted):
        ob.publish(item["id"])
    assert len(ob.publish_log()) == 1
    # A revised version needs, and gets, its own approval.
    item = ob.update_content(item["id"], {"text": "hello"})
    with pytest.raises(NotApproved):
        ob.publish(item["id"])
    h = item["content_hash"].removeprefix("sha256:")[:8]
    ob.approve(item["id"], FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix=h))
    ob.publish(item["id"])
    assert len(ob.publish_log()) == 2


def test_approving_twice_in_the_same_second_gets_distinct_ids():
    ob = Outbox()
    item = ob.propose(kind="post", action="publish", title="t", content={"v": 1}, phase=5)
    ids = []
    for v in (1, 2):
        item = ob.update_content(item["id"], {"v": v})
        h = item["content_hash"].removeprefix("sha256:")[:8]
        ids.append(ob.approve(item["id"], FounderConfirmation(channel="cli", item_ref=item["id"],
                                                               typed_hash_prefix=h))["approval_id"])
    assert len(set(ids)) == 2


def _gate_conf(code, gate=2, attempt=1):
    return FounderConfirmation(channel="cli", item_ref=f"gate{gate}-{attempt}", typed_hash_prefix=code)


def test_gate_codes_are_random_and_used_once():
    a, b = Outbox(), Outbox()
    ca = a.issue_gate_code(2, 1)
    assert re.fullmatch(r"[0-9a-f]{6}", ca)
    assert len({b.issue_gate_code(2, n) for n in range(20)}) > 1     # random, not a function of gate and attempt
    wrong = "000000" if ca != "000000" else "111111"
    with pytest.raises(InvalidConfirmation):
        a.record_gate_decision(gate=2, attempt=1, decision="go", recommended="go", confirmation=_gate_conf(wrong))
    a.record_gate_decision(gate=2, attempt=1, decision="go", recommended="go", confirmation=_gate_conf(ca))
    with pytest.raises(InvalidConfirmation):
        a.record_gate_decision(gate=2, attempt=1, decision="stop", recommended="go", confirmation=_gate_conf(ca))
    assert a.gate_decision(2, 1)["decision"] == "go"


def test_gate_code_cannot_be_derived_from_the_gate_number():
    import hashlib
    ob = Outbox()
    old_style = hashlib.sha256(b"gate1-1").hexdigest()[:6]
    code = ob.issue_gate_code(1, 1)
    if code != old_style:
        with pytest.raises(InvalidConfirmation):
            ob.record_gate_decision(gate=1, attempt=1, decision="go", recommended="go",
                                    confirmation=_gate_conf(old_style, gate=1))


def test_rejection_needs_a_confirmation_for_the_current_version():
    ob = Outbox()
    item = ob.propose(kind="post", action="publish", title="t", content={"text": "hi"}, phase=5)
    with pytest.raises(InvalidConfirmation):
        ob.reject(item["id"], "no", FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix="000000"))
    with pytest.raises(InvalidConfirmation):
        ob.reject(item["id"], "no", FounderConfirmation(channel="cli", item_ref="ob_99", typed_hash_prefix="000000"))
    h = item["content_hash"].removeprefix("sha256:")[:8]
    ob.reject(item["id"], "no", FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix=h))
    assert ob.get(item["id"])["status"] == "rejected"


def test_a_gate_paused_before_codes_were_issued_gets_a_new_code(make_runtime):
    import hashlib
    from conftest import drive
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    # Simulate an older run: no code on record in the outbox, the old derivable code in the checkpoint.
    rt.ctx.outbox._db.execute("DELETE FROM gate_codes")
    rt.ctx.outbox._db.commit()
    old = hashlib.sha256(f"gate1-{p['attempt']}".encode()).hexdigest()[:6]
    fresh = rt.pending()["code"]
    assert fresh and fresh == rt.pending()["code"]            # issued once, stable while it waits
    if fresh != old:
        with pytest.raises(InvalidConfirmation):
            rt.decide_gate(p["recommended"], FounderConfirmation(channel="cli", item_ref=f"gate1-{p['attempt']}",
                                                                  typed_hash_prefix=old), variant_id=p.get("suggested_variant"))
    rt.decide_gate(p["recommended"], FounderConfirmation(channel="cli", item_ref=f"gate1-{p['attempt']}",
                                                          typed_hash_prefix=fresh), variant_id=p.get("suggested_variant"))
    assert rt.ctx.outbox.gate_decision(1, p["attempt"])["decision"] == p["recommended"]


def test_outbox_reads_are_safe_while_another_thread_writes(tmp_path):
    """Regression: the web UI reads the outbox while the background step writes to it on the same SQLite
    connection; unserialized reads saw half-written state (KeyError, 'bad parameter or other API misuse')."""
    import threading
    ob = Outbox(tmp_path / "outbox.db")
    errors, stop = [], threading.Event()

    def writer():
        try:
            for i in range(120):
                it = ob.propose(kind="post", action="publish", title="t", content={"i": i}, phase=5)
                ob.update_content(it["id"], {"i": i, "v": 2})
                with pytest.raises(InvalidConfirmation):
                    ob.approve(it["id"], FounderConfirmation(channel="cli", item_ref=it["id"], typed_hash_prefix="000000"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"writer: {e!r}")
        finally:
            stop.set()

    def reader():
        while not stop.is_set():
            try:
                ob.items()
                ob.publish_log()
            except Exception as e:  # noqa: BLE001
                errors.append(f"reader: {e!r}")
                return

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert len(ob.items()) == 120
