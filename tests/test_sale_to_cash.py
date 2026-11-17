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
