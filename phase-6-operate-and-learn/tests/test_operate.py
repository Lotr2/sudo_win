"""Smoke tests for Phase 6 (Operate and learn). A scripted fake model stands in for gpt-oss. The store database is
built with Phase 5's own schema, so metrics are computed against the real tables.

Run: python -m unittest discover -s phase-6-operate-and-learn/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.append(str(Path(__file__).resolve().parents[2] / "phase-5-launch"))  # last, so phase 6 modules win

import support  # noqa: E402
from common.llm import LLMResult  # noqa: E402
from gates import TargetsTampered, default_targets, review_gate  # noqa: E402
from orchestrator import OperatePipeline, break_even  # noqa: E402
from store_db import connect  # noqa: E402  (Phase 5 schema)

OPENED = datetime(2026, 10, 1, tzinfo=timezone.utc)
MEMO = {"headline": "h", "what_happened": "w",
        "assumptions": [{"assumption": "Groomers will pay", "verdict": "refuted", "evidence": "0 paying"}],
        "learnings": ["Groomers liked the idea but not the price"], "support_themes": [], "next_steps": ["x"],
        "next_experiment": {"hypothesis": "", "metric": "", "target": ""},
        "pivot_options": [{"change": "customer", "option": "Target grooming salons with 3+ staff", "evidence": "tickets"}],
        "wind_down": ["Stop new sales"]}
REPLY = {"category": "question", "summary": "Customer jane@x.com asks where data is stored",
         "reply": "Your data is stored only in your browser on this device.", "internal_note": "",
         "feature_request": None, "bug": None}


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
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"Unexpected extra prompt: {prompt!r}")
        return self.answers.pop(0)


class Base(unittest.TestCase):
    costs = 20  # $19/month and $20/month costs -> break-even 2 customers

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.outputs = self.tmp / "outputs"
        self.outputs.mkdir()
        (self.outputs / "phase3_step4_user_docs.md").write_text("# Guide\n## Your data\nStored in your browser.\n")
        (self.outputs / "phase4_step2_refund_policy.md").write_text("Refunds within 14 days. Not legal advice.\n")
        (self.outputs / "phase5_step5_handoff.json").write_text(json.dumps({
            "brief": {"idea": "Booking for groomers", "skills": "Python", "budget": "500", "time": "10h",
                      "target_market": "groomers", "values": "honesty"},
            "pricing": {"price": 19, "billing": "monthly", "monthly_costs": self.costs, "currency": "USD"},
            "canvas": {"riskiest_assumptions": [{"assumption": "Groomers will pay"}]},
            "spec": {"summary": "Client list"}, "policies": {"refunds": "phase4_step2_refund_policy.md"},
            "store": {"business_name": "Ana Tools", "contact_email": "help@ana.example"},
            "store_opened_at": OPENED.strftime("%Y-%m-%dT%H:%M:%SZ")}))
        self.db = connect(self.tmp / "runtime" / "store" / "store.db")
        self.out = []

    def tearDown(self):
        self.db.close()

    def shop(self, visits=0, paid=0, refunded=0, failed=0):
        for _ in range(visits):
            self.db.execute("INSERT INTO visits (at) VALUES ('2026-10-02T00:00:00Z')")
        n = 0
        for status, count in (("paid", paid), ("refunded", refunded), ("failed", failed)):
            for _ in range(count):
                n += 1
                oid = f"o{n}"
                self.db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                                (oid, f"buyer{n}@example.com", 1900, "USD", status, f"cs{n}", None, "2026-10-02T00:00:00Z"))
                if status != "failed":
                    self.db.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?)",
                                    (f"p{n}", oid, 1900, 85, 1900 if status == "refunded" else 0, None, "2026-10-02T00:00:00Z"))

    def pipe(self, replies=(), answers=(), days=40):
        self.llm = FakeLLM(replies)
        self.founder = Founder(answers)
        return OperatePipeline(self.llm, input_fn=self.founder, output_fn=self.out.append,
                               log_path=self.tmp / "log.json", outputs_dir=self.outputs,
                               runtime_dir=self.tmp / "runtime", now=OPENED + timedelta(days=days))

    def lock_targets(self):
        self.assertIsNotNone(self.pipe(answers=["a"]).set_targets())

    def log(self):
        return json.loads((self.tmp / "log.json").read_text())


class TargetsTest(Base):
    def test_defaults_come_from_break_even_and_are_locked(self):
        self.lock_targets()
        locked = json.loads((self.outputs / "phase6_step1_targets.json").read_text())
        self.assertEqual(locked["targets"]["target_paying_customers"], 2)
        self.assertTrue(any(e["action"] == "approve_review_targets" for e in self.log()))

    def test_invalid_targets_cannot_be_approved(self):
        p = self.pipe(answers=["e", "", "3", "5", "", "", "a", "e", "", "", "1", "", "", "a"])
        p.set_targets()
        self.assertIn("Please type one of: e, s", self.out)

    def test_review_needs_targets_first(self):
        with self.assertRaises(FileNotFoundError):
            self.pipe().review()

    def test_moved_targets_are_refused(self):
        self.lock_targets()
        path = self.outputs / "phase6_step1_targets.json"
        locked = json.loads(path.read_text())
        locked["targets"]["target_paying_customers"] = 0
        path.write_text(json.dumps(locked))
        with self.assertRaises(TargetsTampered):
            self.pipe().review()


class ReviewTest(Base):
    def test_persevere_when_target_reached(self):
        self.lock_targets()
        self.shop(visits=150, paid=3)
        result = self.pipe([MEMO], ["c"]).review()
        self.assertEqual(result, {"outcome": "persevere", "decision": "persevere"})
        memo = (self.outputs / "phase6_step4_memo.md").read_text()
        self.assertIn("Decision rule outcome: PERSEVERE", memo)
        self.assertIn("| Groomers will pay | REFUTED | 0 paying |", memo)

    def test_stop_offers_no_way_to_persevere(self):
        self.lock_targets()
        self.shop(visits=150, paid=0, failed=2)
        result = self.pipe([MEMO], ["c", "s"]).review()
        self.assertEqual(result, {"outcome": "stop", "decision": "stop"})
        self.assertIn("Please type one of: p, s", self.out)
        self.assertIn("**Outcome:** STOP", (self.outputs / "phase6_gate_review.md").read_text())

    def test_no_verdict_while_window_open_or_traffic_low(self):
        self.lock_targets()
        self.shop(visits=150, paid=1)
        self.assertEqual(self.pipe([MEMO], ["k"], days=5).review()["outcome"], "repeat")
        self.assertEqual(review_gate(default_targets(2), {
            "paying_customers": 0, "refund_rate": None, "store_visitors": 20, "days_open": 60, "ever_paid": 0})
            .outcome, "repeat")

    def test_high_refund_rate(self):
        t = default_targets(4)
        m = {"paying_customers": 2, "refund_rate": 0.5, "store_visitors": 300, "days_open": 60, "ever_paid": 4}
        g = review_gate(t, m)
        self.assertEqual(g.outcome, "pivot")
        self.assertIn("Refund rate too high", g.reasons[0])

    def test_analyst_cannot_change_the_outcome(self):
        self.lock_targets()
        self.shop(visits=150)
        self.pipe([{**MEMO, "headline": "Persevere! Everything is great"}], ["s"]).review()
        self.assertIn("## outcome (fixed)\nstop", self.llm.calls[0])
        self.assertIn("Decision rule outcome: STOP", (self.outputs / "phase6_step4_memo.md").read_text())


class PivotTest(Base):
    costs = 60  # break-even 4 -> target 4, stop line 2

    def test_pivot_writes_a_new_phase1_brief(self):
        self.lock_targets()
        self.shop(visits=150, paid=2)
        result = self.pipe([MEMO], ["p", "1"]).review()
        self.assertEqual(result["outcome"], "pivot")
        brief = json.loads((self.outputs / "phase6_step5_next_brief.json").read_text())
        self.assertIn("Target grooming salons with 3+ staff", brief["idea"])
        self.assertIn("not the price", brief["idea"])
        self.assertEqual(brief["skills"], "Python")


class SupportAndMetricsTest(Base):
    def test_support_reply_needs_founder_and_is_mock_sent(self):
        self.shop(paid=1)
        result = self.pipe([REPLY], ["a"]).support_reply("buyer1@example.com", "Where is my data? Call me on +1 555 123 4567")
        self.assertEqual(result["status"], "replied")
        sent = list((self.tmp / "runtime" / "support" / "sent").iterdir())
        self.assertIn("MOCKED: not sent by email", sent[0].read_text())
        self.assertIn('"status": "paid"', self.llm.calls[0])  # grounded in this customer's order
        summary = (self.outputs / "phase6_step2_support_summary.md").read_text()
        self.assertNotIn("jane@x.com", summary)
        self.assertIn("[email]", summary)

    def test_reply_promising_a_refund_is_rewritten(self):
        bad = {**REPLY, "category": "refund_request", "reply": "No problem, I've refunded you in full."}
        self.pipe([bad, REPLY], ["a"]).support_reply(None, "I want my money back")
        self.assertIn("a person reviews every refund", self.llm.calls[1])

    def test_founder_edit_is_checked(self):
        self.pipe([REPLY], ["e", "We'll add that next week, guaranteed!", "e", "Thanks, noted.", "a"]).support_reply(
            None, "Can you add SMS?")
        self.assertTrue(any("must not promise" in o for o in self.out))

    def test_discarded_reply_is_not_sent(self):
        self.pipe([REPLY], ["x"]).support_reply(None, "hello")
        self.assertFalse((self.tmp / "runtime" / "support" / "sent").exists())

    def test_metrics_are_aggregate_only(self):
        self.shop(visits=200, paid=3, refunded=1, failed=1)
        wl = self.tmp / "runtime" / "waitlist"
        wl.mkdir(parents=True)
        (wl / "waitlist.csv").write_text("2026-09-01T00:00:00Z,buyer1@example.com\n2026-09-01T00:00:00Z,other@example.com\n")
        m = self.pipe().write_metrics()
        self.assertEqual((m["paying_customers"], m["ever_paid"], m["refund_rate"]), (3, 4, 0.25))
        self.assertEqual((m["waitlist_size"], m["buyers_from_waitlist"]), (2, 1))
        self.assertEqual(m["visit_to_purchase"], 0.02)
        text = (self.outputs / "phase6_step3_metrics.md").read_text()
        self.assertNotIn("@", text)
        self.assertIn("MOCKED", text)

    def test_break_even_and_redaction(self):
        self.assertEqual(break_even({"price": 120, "billing": "yearly", "monthly_costs": 25}), 3)
        self.assertEqual(support.redact("mail a@b.co or +44 20 7946 0958"), "mail [email] or [phone]")


if __name__ == "__main__":
    unittest.main()
