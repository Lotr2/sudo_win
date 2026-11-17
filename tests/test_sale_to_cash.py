"""Sale to cash (all MOCKED): payment delivers once, duplicates are ignored, refunds need the founder and
revoke access, reconciliation flags a broken record."""

import pytest

from venture_builder import reconcile, webhooks
from venture_builder.approval import FounderConfirmation, NotApproved, Outbox
from venture_builder.delivery import has_access
from venture_builder.events import EventStore
from venture_builder.payments import MockPaymentProvider
from venture_builder.refunds import execute_refund, request_refund


@pytest.fixture
def shop():
    store = EventStore()
    return store, MockPaymentProvider(store), Outbox()


def _token(store, oid):
    return store.rows("SELECT token FROM deliveries WHERE order_id=?", (oid,))[0]["token"]


def test_one_payment_delivers_once(shop):
    store, pay, _ = shop
    oid = pay.create_checkout("Kit", 19)
    assert store.rows("SELECT COUNT(*) AS n FROM deliveries")[0]["n"] == 0
    assert webhooks.handle(store, pay.succeed(oid)) == "delivered"
    assert store.rows("SELECT COUNT(*) AS n FROM deliveries WHERE order_id=?", (oid,))[0]["n"] == 1
    assert has_access(store.db, _token(store, oid))


def test_duplicate_event_does_not_deliver_twice(shop):
    store, pay, _ = shop
    oid = pay.create_checkout("Kit", 19)
    ev = pay.succeed(oid)
    webhooks.handle(store, ev)
    assert webhooks.handle(store, ev) == "duplicate"
    assert webhooks.handle(store, dict(ev)) == "duplicate"
    assert store.rows("SELECT COUNT(*) AS n FROM deliveries")[0]["n"] == 1


def _approve(ob, item):
    h = item["content_hash"].removeprefix("sha256:")[:8]
    ob.approve(item["id"], FounderConfirmation(channel="cli", item_ref=item["id"], typed_hash_prefix=h))


def test_refund_revokes_access(shop):
    store, pay, ob = shop
    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    token = _token(store, oid)
    item = request_refund(ob, oid, "asked")
    _approve(ob, item)
    assert execute_refund(ob, store, item["id"]) == "revoked"
    assert not has_access(store.db, token)


def test_refund_without_founder_approval_does_nothing(shop):
    store, pay, ob = shop
    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    item = request_refund(ob, oid, "asked")
    with pytest.raises(NotApproved):
        execute_refund(ob, store, item["id"])
    assert has_access(store.db, _token(store, oid))
    assert store.rows("SELECT COUNT(*) AS n FROM events WHERE type='refund.succeeded'")[0]["n"] == 0


def test_reconciliation_flags_a_broken_record(shop):
    store, pay, _ = shop
    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    pay.payout()
    assert reconcile.reconcile(store)["mismatches"] == []
    store.db.execute("INSERT INTO deliveries VALUES ('ord_999','x','active','t',NULL)")
    assert any("ord_999" in m for m in reconcile.reconcile(store)["mismatches"])


def test_event_log_is_append_only(shop):
    store, pay, _ = shop
    pay.create_checkout("Kit", 19)
    with pytest.raises(Exception):
        store.db.execute("DELETE FROM events")


# ---- one approval refunds once; the order state machine is enforced ----

def test_one_approval_refunds_only_once(shop):
    from venture_builder.approval import AlreadyExecuted
    store, pay, ob = shop
    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    item = request_refund(ob, oid, "asked")
    _approve(ob, item)
    assert execute_refund(ob, store, item["id"]) == "revoked"
    with pytest.raises(AlreadyExecuted):
        execute_refund(ob, store, item["id"])
    with pytest.raises(AlreadyExecuted):
        ob.publish(item["id"])
    assert store.rows("SELECT COUNT(*) AS n FROM events WHERE type='refund.succeeded'")[0]["n"] == 1
    assert len(ob.publish_log()) == 1
    assert reconcile.reconcile(store)["mismatches"] == []


def test_replayed_refund_event_is_not_stored_twice(shop):
    store, pay, _ = shop
    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    first = pay.refund_event(oid, refund_id="ob_01")
    again = pay.refund_event(oid, refund_id="ob_01")
    assert first["event_id"] == again["event_id"]
    assert store.rows("SELECT COUNT(*) AS n FROM events WHERE type='refund.succeeded'")[0]["n"] == 1
    assert webhooks.handle(store, first) == "revoked"
    assert webhooks.handle(store, again) == "duplicate"


def test_unpaid_order_cannot_be_refunded(shop):
    from venture_builder.refunds import RefundNotAllowed
    store, pay, ob = shop
    oid = pay.create_checkout("Kit", 19)          # never paid
    item = request_refund(ob, oid, "asked")
    _approve(ob, item)
    with pytest.raises(RefundNotAllowed):
        execute_refund(ob, store, item["id"])
    assert ob.get(item["id"])["status"] == "approved"    # approval not spent
    assert store.rows("SELECT COUNT(*) AS n FROM events WHERE type='refund.succeeded'")[0]["n"] == 0


def test_webhook_rejects_events_that_break_the_order_state_machine(shop):
    store, pay, _ = shop
    unpaid = pay.create_checkout("Kit", 19)
    assert webhooks.handle(store, pay.refund_event(unpaid, refund_id="x1")) == "rejected"
    assert store.rows("SELECT status FROM orders WHERE order_id=?", (unpaid,))[0]["status"] == "pending"

    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    token = _token(store, oid)
    assert webhooks.handle(store, pay.refund_event(oid, refund_id="x2")) == "revoked"
    # A late payment event after the refund must not reopen the order or restore access.
    assert webhooks.handle(store, pay.succeed(oid)) == "rejected"
    assert store.rows("SELECT status FROM orders WHERE order_id=?", (oid,))[0]["status"] == "refunded"
    assert not has_access(store.db, token)
    assert webhooks.handle(store, {"event_id": "evt_x", "type": "payment.succeeded", "order_id": "ord_999"}) == "rejected"


def test_reconciliation_flags_refunds_without_payment_or_above_it(shop):
    store, pay, _ = shop
    unpaid = pay.create_checkout("Kit", 19)
    store.append("refund.succeeded", unpaid, -19)
    oid = pay.create_checkout("Kit", 19)
    webhooks.handle(store, pay.succeed(oid))
    store.append("refund.succeeded", oid, -19)
    store.append("refund.succeeded", oid, -19)
    m = reconcile.reconcile(store)["mismatches"]
    assert any(unpaid in x and "refund with no payment" in x for x in m)
    assert any(oid in x and "more than" in x for x in m)
    assert any(oid in x and "2 refunds" in x for x in m)


def test_sequential_ids_stay_unique_across_two_connections(tmp_path):
    a, b = EventStore(tmp_path / "ev.db"), EventStore(tmp_path / "ev.db")
    ids = {MockPaymentProvider(s).create_checkout("Kit", 1) for s in (a, b, a, b)}
    assert len(ids) == 4
