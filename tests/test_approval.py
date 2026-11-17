"""Nothing goes out without founder approval."""
import pytest

import approval
from approval import ApprovalBroker, ApprovalPending, ApprovalRequired
from core import outbox
from core.log import DecisionLog


def _broker(tmp_path, decisions):
    return ApprovalBroker(DecisionLog(tmp_path / "log.json"), decisions)


def test_publish_without_receipt_raises(tmp_path):
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "outbox", "x", content="hello", key="k1")
    assert not (tmp_path / "outbox").exists()


def test_approved_receipt_is_single_use(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a.publish": "approve"}})
    r = b.request("idea2", "a.publish", "publish", "s", "hello")
    outbox.publish(tmp_path / "outbox", "x", content="hello", key="k1", receipt=r)
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "outbox", "x", content="hello", key="k2", receipt=r)


def test_receipt_bound_to_content_and_kind(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a": "approve", "b": "approve"}})
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "o", "x", content="changed", key="k", receipt=b.request("idea2", "a", "publish", "s", "orig"))
    with pytest.raises(ApprovalRequired):
        outbox.send(tmp_path / "o", "x", content="orig", key="k", receipt=b.request("idea2", "b", "publish", "s", "orig"))


def test_forged_receipt_rejected(tmp_path):
    fake = approval.Receipt("a", "publish", approval.payload_hash("x"), "n0nce", "0" * 64)
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "o", "x", content="x", key="k", receipt=fake)


def test_rejection_returns_none_and_is_logged(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a": "reject"}})
    assert b.request("idea2", "a", "publish", "s", "x") is None
    assert b.log.entries[-1]["action"] == "reject:a" and b.log.entries[-1]["approved_by"] == "founder"


def test_missing_decision_pauses_never_auto_approves(tmp_path):
    with pytest.raises(ApprovalPending):
        _broker(tmp_path, {}).request("idea2", "a", "publish", "s", "x")


def test_outbox_key_is_written_once(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a": "approve", "b": "approve"}})
    outbox.publish(tmp_path / "o", "x", content="c", key="idea2__a", receipt=b.request("idea2", "a", "publish", "s", "c"))
    with pytest.raises(outbox.DuplicateOutward):
        outbox.publish(tmp_path / "o", "x", content="c", key="idea2__a", receipt=b.request("idea2", "b", "publish", "s", "c"))
    assert len(list((tmp_path / "o").iterdir())) == 1


def test_decisions_are_per_attempt_and_never_replayed(tmp_path):
    from core.state import RunState
    state = RunState(tmp_path / "state.json")
    b = ApprovalBroker(DecisionLog(tmp_path / "l1.json"), {"idea2": {"a": "approve", "g": "revise"}}, state=state)
    assert b.request("idea2", "a", "publish", "s", "same text") is not None
    with pytest.raises(ApprovalPending):           # attempt 2 needs its own decision ("a@2"), even for the same text
        b.request("idea2", "a", "publish", "s", "same text", attempt=2)
    again = ApprovalBroker(DecisionLog(tmp_path / "l2.json"), {}, state=RunState(tmp_path / "state.json"))
    with pytest.raises(ApprovalPending):           # a stored decision is an audit record, not an answer
        again.request("idea2", "a", "publish", "s", "same text")
    d = b.decide("idea2", "g", "gate", "s", "body", attempt=1, choices=["accept", "revise", "stop"])
    assert d.choice == "revise"
    with pytest.raises(ApprovalPending):
        b.decide("idea2", "g", "gate", "s", "body", attempt=2, choices=["accept", "revise", "stop"])


def test_decisions_file_note_and_attempt_keys(tmp_path):
    b = ApprovalBroker(DecisionLog(tmp_path / "l.json"),
                       {"idea2": {"g@2": {"choice": "redo", "note": "shorter copy"}}})
    d = b.decide("idea2", "g", "gate", "s", "body", attempt=2, choices=["redo", "stop"])
    assert (d.choice, d.note) == ("redo", "shorter copy")


def test_gate_choice_must_be_allowed_at_this_gate(tmp_path):
    b = ApprovalBroker(DecisionLog(tmp_path / "l.json"), {"idea2": {"phase6.gate": "revise"}})
    with pytest.raises(ApprovalPending, match="not one of"):
        b.decide("idea2", "phase6.gate", "gate", "s", "body", choices=["accept", "go", "pivot", "stop"])
