"""Sale to cash (all MOCKED): payment triggers delivery once, refund needs the founder and revokes access, books balance."""
import pytest

import delivery
import payments
import reconcile
import refunds
import webhooks
from approval import ApprovalBroker, ApprovalRequired
from core.log import DecisionLog


@pytest.fixture
def store(tmp_path):
    b = ApprovalBroker(DecisionLog(tmp_path / "l.json"), {"i": {"open": "approve", "refund": "approve"}})
    st = payments.CommerceState()
    payments.open_store(st, "pack", 19, content="page", receipt=b.request("i", "open", "open_store", "s", "page"))
    return st, b


def test_store_cannot_open_without_approval():
    with pytest.raises(ApprovalRequired):
        payments.open_store(payments.CommerceState(), "pack", 19, content="page")


def test_payment_delivers_exactly_once(store):
    st, _ = store
    body, sig = webhooks.sign(payments.simulate_test_order(st, "o1"))
    first, second = webhooks.handle(st, body, sig), webhooks.handle(st, body, sig)
    assert first["status"] == "delivered" and second["status"] == "duplicate_ignored"
    assert len(st.deliveries) == 1 and delivery.can_download(st, first["token"])


def test_bad_signature_rejected(store):
    st, _ = store
    body, _sig = webhooks.sign(payments.simulate_test_order(st, "o1"))
    with pytest.raises(PermissionError):
        webhooks.handle(st, body, "0" * 64)


def test_refund_requires_founder_and_revokes(store):
    st, b = store
    body, sig = webhooks.sign(payments.simulate_test_order(st, "o2"))
    token = webhooks.handle(st, body, sig)["token"]
    prop = refunds.propose(st, "o2", "test")
    with pytest.raises(ApprovalRequired):
        refunds.execute(st, "o2", content=prop["content"])
    refunds.execute(st, "o2", content=prop["content"], receipt=b.request("i", "refund", "refund", "s", prop["content"]))
    assert not delivery.can_download(st, token)


def test_reconciliation_balances(store):
    st, b = store
    for oid in ("o1", "o2"):
        body, sig = webhooks.sign(payments.simulate_test_order(st, oid))
        webhooks.handle(st, body, sig)
    prop = refunds.propose(st, "o2", "test")
    refunds.execute(st, "o2", content=prop["content"], receipt=b.request("i", "refund", "refund", "s", prop["content"]))
    payments.mock_payout(st)
    _rows, balanced = reconcile.reconcile(st)
    assert balanced
