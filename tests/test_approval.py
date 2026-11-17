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
        outbox.publish(tmp_path / "outbox", "x", content="hello")
    assert not (tmp_path / "outbox").exists()


def test_approved_receipt_is_single_use(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a.publish": "approve"}})
    r = b.request("idea2", "a.publish", "publish", "s", "hello")
    outbox.publish(tmp_path / "outbox", "x", content="hello", receipt=r)
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "outbox", "x", content="hello", receipt=r)


def test_receipt_bound_to_content_and_kind(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a": "approve", "b": "approve"}})
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "o", "x", content="changed", receipt=b.request("idea2", "a", "publish", "s", "orig"))
    with pytest.raises(ApprovalRequired):
        outbox.send(tmp_path / "o", "x", content="orig", receipt=b.request("idea2", "b", "publish", "s", "orig"))


def test_forged_receipt_rejected(tmp_path):
    fake = approval.Receipt("a", "publish", approval.payload_hash("x"), "n0nce", "0" * 64)
    with pytest.raises(ApprovalRequired):
        outbox.publish(tmp_path / "o", "x", content="x", receipt=fake)


def test_rejection_returns_none_and_is_logged(tmp_path):
    b = _broker(tmp_path, {"idea2": {"a": "reject"}})
    assert b.request("idea2", "a", "publish", "s", "x") is None
    assert b.log.entries[-1]["action"] == "reject:a" and b.log.entries[-1]["approved_by"] == "founder"


def test_missing_decision_pauses_never_auto_approves(tmp_path):
    with pytest.raises(ApprovalPending):
        _broker(tmp_path, {}).request("idea2", "a", "publish", "s", "x")
