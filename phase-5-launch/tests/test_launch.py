"""Smoke tests for Phase 5 (Launch). A scripted fake model stands in for gpt-oss; the store, MOCKED payment
provider, webhooks, delivery, refunds and reconciliation are real and run over real HTTP.

Run: python -m unittest discover -s phase-5-launch/tests
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import refunds  # noqa: E402
import webhooks  # noqa: E402
from agents.writers import check_post  # noqa: E402
from checkout import start_checkout  # noqa: E402
from common.llm import LLMResult  # noqa: E402
from orchestrator import LaunchPipeline  # noqa: E402
from server import make_server  # noqa: E402

COPY = {"headline": "Stop losing track of your grooming clients",
        "subheadline": "A simple client list for solo groomers, in your browser.",
        "who_its_for": "Solo dog groomers.", "faq": [{"q": "Where is my data?", "a": "Only in your browser."}]}
POSTS = {"posts": [
    {"channel": "Groomer Facebook group", "title": "I built a tiny client list for groomers",
     "body": "I built this after watching my sister lose bookings. It's early. Feedback welcome: {store_link}",
     "etiquette": "Check the group's self-promotion rules first."},
    {"channel": "r/doggrooming", "title": "", "body": "I made a simple client list app. $19/month. {store_link}",
     "etiquette": "Post in the weekly promo thread."}]}


def handoff(tmp: Path) -> dict:
    product = tmp / "product"
    product.mkdir()
    (product / "index.html").write_text("<!doctype html><title>Client book</title><p>The app</p>")
    (product / "logic.js").write_text("export function createState() { return {}; }\n")
    (product / "logic.test.js").write_text("// tests\n")
    outputs = tmp / "outputs"
    outputs.mkdir()
    files = {"terms": "phase4_step2_terms_of_service.md", "privacy": "phase4_step2_privacy_policy.md",
             "refunds": "phase4_step2_refund_policy.md"}
    for name, f in files.items():
        (outputs / f).write_text(f"# {name}\n\nDRAFT. This is not legal advice.\n")
    h = {"spec": {"product_name": "Client book", "summary": "Client list",
                  "stories": [{"story": "As a groomer, I can add clients so that I remember them"}],
                  "out_of_scope": ["SMS reminders"]},
         "canvas": {"channels": ["Groomer Facebook groups"]}, "brief": {"values": "honesty"},
         "pricing": {"price": 19, "currency": "USD", "billing": "monthly", "refund_days": 14},
         "price_line": "$19 per month", "billing_note": "Renews every month at $19 until you cancel.",
         "refund_note": "If it isn't right for you, ask for a full refund within 14 days of paying.",
         "business": {"business_name": "Ana Tools", "contact_email": "help@ana.example"},
         "policies": files, "product_dir": str(product)}
    (outputs / "phase4_step4_handoff.json").write_text(json.dumps(h))
    return h


class FakeLLM:
    model = "fake-gpt-oss"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_json(self, system, user, required_keys=()):
        self.calls.append(user)
        return LLMResult(self.replies.pop(0), self.model, 10, 5)


class Founder:
    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, prompt=""):
        if not self.answers:
            raise AssertionError(f"Unexpected extra prompt: {prompt!r}")
        return self.answers.pop(0)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.h = handoff(self.tmp)
        self.out = []

    def pipe(self, replies=(), answers=()):
        self.llm = FakeLLM(replies)
        return LaunchPipeline(self.llm, input_fn=Founder(answers), output_fn=self.out.append,
                              log_path=self.tmp / "log.json", outputs_dir=self.tmp / "outputs",
                              runtime_dir=self.tmp / "runtime")

    def log(self):
        return json.loads((self.tmp / "log.json").read_text())

    def prepared(self):
        p = self.pipe([COPY, POSTS], ["p", "a", "x", "a"])
        self.assertEqual(p.prepare()["outcome"], "prepared")
        return p


class PrepareTest(Base):
    def test_prepare_needs_founder_approval_at_every_step(self):
        self.prepared()
        actions = {e["action"] for e in self.log() if e["approved_by"] == "founder"}
        self.assertTrue({"publish_store_page", "approve_launch_post", "discard_launch_post", "review_launch_posts",
                         "approve_delivery_rule"} <= actions)
        page = (self.tmp / "outputs" / "phase5_step1_store_page.html").read_text()
        for text in ("MOCKED CHECKOUT", "$19 per month", "Add clients", "SMS reminders", "/policies/refunds"):
            self.assertIn(text, page)
        posts = (self.tmp / "outputs" / "phase5_step2_launch_posts.md").read_text()
        self.assertIn("(APPROVED)", posts)
        self.assertIn("(DISCARDED)", posts)
        self.assertIn("does not post", posts)

    def test_stopping_at_store_page_publishes_nothing(self):
        self.assertEqual(self.pipe([COPY], ["s"]).prepare()["outcome"], "stop")
        self.assertFalse((self.tmp / "runtime" / "store" / "index.html").exists())

    def test_dishonest_store_copy_is_regenerated(self):
        bad = {**COPY, "subheadline": "Trusted by 2,000 groomers. Only 5 spots left!"}
        self.pipe([bad, COPY, POSTS], ["p", "a", "a", "a"]).prepare()
        self.assertNotIn("2,000", "".join(self.out))
        self.assertIn("rejected", self.llm.calls[1])

    def test_posts_must_be_in_the_founders_own_voice(self):
        fake = {"posts": [{"channel": "Reddit", "title": "", "etiquette": "",
                           "body": "As a happy customer I found this tool and it's great: {store_link}"}]}
        self.pipe([COPY, fake, POSTS], ["p", "a", "a", "a"]).prepare()
        self.assertIn("written as the founder", self.llm.calls[2])

    def test_founder_edits_to_posts_are_checked(self):
        p = self.pipe([COPY, POSTS], ["p", "e", "Buy now, limited time! https://x.com", "e",
                                      "I built this, feedback welcome {store_link}", "a", "x", "a"])
        p.prepare()
        self.assertTrue(any("post 1:" in o for o in self.out))
        self.assertIn("feedback welcome", (self.tmp / "outputs" / "phase5_step2_launch_posts.md").read_text())

    def test_check_post(self):
        self.assertEqual(check_post({"title": "", "body": "I built a thing. {store_link}"}), [])
        self.assertTrue(check_post({"title": "", "body": "Great tool, join 500+ users! www.x.com"}))


class PaymentTest(Base):
    def test_test_run_passes_every_scenario(self):
        p = self.prepared()
        run = p.test_run()
        self.assertTrue(run["passed"], [r for r in run["results"] if not r["passed"]])
        self.assertEqual(len(run["results"]), 9)
        log = (self.tmp / "outputs" / "payment_test_log.md").read_text()
        self.assertIn("**Payments are MOCKED.**", log)
        self.assertIn("ALL PASSED", log)

    def test_test_run_requires_approved_delivery_rule(self):
        p = self.pipe([COPY, POSTS], ["p", "a", "a", "s"])
        p.prepare()
        import delivery
        with self.assertRaises(delivery.DeliveryNotApproved):
            p.test_run()

    def test_gate_go_only_after_test_run(self):
        p = self.prepared()
        p.input_fn = Founder(["s"])
        self.assertEqual(p.gate()["outcome"], "repeat")
        self.assertFalse((self.tmp / "outputs" / "phase5_step5_handoff.json").exists())
        p.test_run()
        p.input_fn = Founder(["o"])
        self.assertEqual(p.gate(), {"outcome": "go", "decision": "open_store"})
        self.assertTrue(json.loads((self.tmp / "outputs" / "phase5_step5_handoff.json").read_text())["payment_test"]["passed"])

    def test_edited_store_page_fails_the_gate(self):
        p = self.prepared()
        p.test_run()
        page = self.tmp / "runtime" / "store" / "index.html"
        page.write_text(page.read_text().replace("$19 per month", "$9 per month (was $49!)"))
        p.input_fn = Founder(["s"])
        self.assertEqual(p.gate()["outcome"], "repeat")


class LiveStoreTest(Base):
    def setUp(self):
        super().setUp()
        self.p = self.prepared()
        self.store = self.p.store()
        self.server = make_server(self.store, "127.0.0.1", 0)
        self.store.base_url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def req(self, method, path, form=None, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        data = body if body is not None else (urllib.parse.urlencode(form).encode() if form else None)
        conn.request(method, path, data, headers or ({"Content-Type": "application/x-www-form-urlencoded"} if form else {}))
        r = conn.getresponse()
        return r.status, r.getheader("Location"), r.read().decode(errors="replace")

    def buy(self, email="b@example.com", outcome="success"):
        status, loc, _ = self.req("POST", "/checkout", {"email": email})
        self.assertEqual((status, loc.startswith("/mock-checkout/")), (303, True))
        status, page_loc, _ = self.req("POST", f"/mock-pay/{loc.split('/')[2]}", {"outcome": outcome})
        return page_loc.split("/")[2]

    def test_buy_open_refund_revoke_over_http(self):
        self.assertIn("MOCKED CHECKOUT", self.req("GET", "/")[2])
        self.req("GET", "/", headers={"Cookie": "sv=1"})  # returning visitor is not counted again
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 1)
        order_id = self.buy()
        _, _, order_page = self.req("GET", f"/order/{order_id}")
        self.assertIn("Open the product", order_page)
        self.assertIn("MOCKED: shown here, not sent", order_page)
        token = self.store.db.execute("SELECT token FROM grants WHERE order_id=?", (order_id,)).fetchone()[0]
        self.assertEqual(self.req("GET", f"/app/{token}/")[0], 200)
        self.assertEqual(self.req("GET", f"/app/{token}/logic.test.js")[0], 404)
        self.assertEqual(self.req("GET", f"/app/{token}/../../outputs/phase4_step4_handoff.json")[0], 404)
        self.assertEqual(self.req("GET", "/app/not-a-token/")[0], 403)

        self.req("POST", "/refund-request", {"order_id": order_id, "email": "b@example.com", "reason": "no"})
        self.assertEqual(self.req("GET", f"/app/{token}/")[0], 200)  # still has access until the founder decides
        self.p.input_fn = Founder(["a"])
        self.assertEqual(self.p.review_refunds(), 1)
        self.assertEqual(self.req("GET", f"/app/{token}/")[0], 403)
        self.assertTrue(any(e["action"] == "approve_refund" and e["approved_by"] == "founder" for e in self.log()))
        self.assertEqual(self.p.reconcile_store()["mismatches"], [])

    def test_failed_payment_gives_no_access(self):
        order_id = self.buy(outcome="fail")
        self.assertIn("payment failed", self.req("GET", f"/order/{order_id}")[2])
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM grants WHERE order_id=?", (order_id,)).fetchone())

    def test_webhook_endpoint_rejects_bad_signatures_and_dedupes(self):
        order_id, checkout_id = start_checkout(self.store.db, self.store.provider, "c@example.com", 1900, "USD")
        payload, sig = self.store.provider.complete_checkout(checkout_id, True)
        self.assertEqual(self.req("POST", "/webhooks/payments", body=payload, headers={"X-Signature": "bad"})[0], 400)
        first = self.req("POST", "/webhooks/payments", body=payload, headers={"X-Signature": sig})
        again = self.req("POST", "/webhooks/payments", body=payload, headers={"X-Signature": sig})
        self.assertEqual((first[2], again[2]), ("processed", "duplicate"))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM outbox WHERE order_id=?", (order_id,)).fetchone()[0], 1)

    def test_refund_needs_matching_email_and_founder(self):
        order_id = self.buy()
        with self.assertRaises(refunds.RefundError):
            refunds.request(self.store.db, order_id, "someone-else@example.com", "x")
        rid = refunds.request(self.store.db, order_id, "b@example.com", "x")
        with self.assertRaises(PermissionError):
            refunds.approve(self.store.db, self.store.provider, rid, approved_by="system")
        with self.assertRaises(webhooks.InvalidSignature):
            webhooks.verify(b"{}", "", self.store.secret)


if __name__ == "__main__":
    unittest.main()
