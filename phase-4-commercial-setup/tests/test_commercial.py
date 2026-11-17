"""Smoke tests for Phase 4 (Commercial setup). A scripted fake model stands in for gpt-oss.

Run: python -m unittest discover -s phase-4-commercial-setup/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policies  # noqa: E402
import pricing  # noqa: E402
from common.llm import LLMResult  # noqa: E402
from gates import commercial_gate  # noqa: E402
from orchestrator import CommercialPipeline  # noqa: E402

HANDOFF = {
    "brief": {"idea": "Booking for groomers", "budget": "500", "values": "honesty"},
    "canvas": {"revenue_streams": "Monthly subscription", "existing_alternatives": "Paper diary"},
    "evidence": {"signups": 120, "visitors": 600},
    "spec": {"product_name": "Client book", "summary": "Keep a list of grooming clients.",
             "stories": [{"id": "S1", "story": "As a groomer I can add clients"}], "out_of_scope": ["SMS reminders"]},
    "product_dir": "runtime/product",
}

PRICE = {"billing": "monthly", "price": 19, "currency": "USD", "refund_days": 14, "monthly_costs": 20,
         "monthly_costs_breakdown": [{"item": "Hosting", "amount": 5}, {"item": "Domain", "amount": 15}],
         "value_metric": "per groomer", "rationale": "Paper diaries cost nothing but lose bookings.",
         "anchors": [{"alternative": "Generic salon software", "price": "$40/month"}],
         "price_test": "Ask waitlist members", "headline": "One simple plan", "includes": ["Client list"],
         "assumptions": ["groomers pay for tools"]}

REVIEW = {"issues": [], "ask_a_lawyer": ["consumer law on subscriptions"], "summary": "Looks consistent."}
REVIEW_FIX = {"issues": [{"policy": "terms", "severity": "fix", "issue": "x", "suggestion": "y"}],
              "ask_a_lawyer": [], "summary": ""}
BUSINESS = ["Ana's Tools", "help@anastools.example", "Ontario, Canada", ""]


class FakeLLM:
    model = "fake-gpt-oss"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_json(self, system, user, required_keys=()):
        self.calls.append(user)
        return LLMResult(self.replies.pop(0), self.model, 10, 5)


class Founder:
    def __init__(self, answers, on_prompt=None):
        self.answers = list(answers)
        self.prompts = []
        self.on_prompt = on_prompt

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if self.on_prompt:
            self.on_prompt(len(self.prompts))
        if not self.answers:
            raise AssertionError(f"Unexpected extra prompt: {prompt!r}")
        return self.answers.pop(0)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.outputs = self.tmp / "outputs"
        self.outputs.mkdir()
        (self.outputs / "phase3_step5_handoff.json").write_text(json.dumps(HANDOFF))
        self.out = []

    def run_phase(self, replies, answers, on_prompt=None, handoff=None):
        if handoff:
            (self.outputs / "phase3_step5_handoff.json").write_text(json.dumps(handoff))
        self.llm = FakeLLM(replies)
        self.founder = Founder(answers, on_prompt)
        p = CommercialPipeline(self.llm, input_fn=self.founder, output_fn=self.out.append,
                               log_path=self.tmp / "log.json", outputs_dir=self.outputs, today="2026-11-17")
        result = p.run()
        self.assertEqual(self.llm.replies, [], "every scripted model reply should have been used")
        return result

    def log(self):
        return json.loads((self.tmp / "log.json").read_text())

    def read(self, key):
        return (self.outputs / policies.FILES[key]).read_text()

    def test_happy_path(self):
        result = self.run_phase([PRICE, REVIEW], ["a", *BUSINESS, "a", "a"])
        self.assertEqual(result["outcome"], "go")
        terms, privacy, refunds = self.read("terms"), self.read("privacy"), self.read("refunds")
        for text in (terms, privacy, refunds):
            self.assertIn("not legal advice", text)
            self.assertIn("help@anastools.example", text)
            self.assertIn("Ontario, Canada", text)
        self.assertIn("$19 (per month)", terms)
        self.assertIn("Renews every month", terms)
        self.assertIn("14 days", refunds)
        self.assertIn("waitlist", privacy.lower())
        self.assertIn("browser", privacy)
        handoff = json.loads((self.outputs / "phase4_step4_handoff.json").read_text())
        self.assertEqual(handoff["price_line"], "$19 per month")
        self.assertEqual(handoff["spec"]["product_name"], "Client book")
        actions = [e["action"] for e in self.log() if e["approved_by"] == "founder"]
        for a in ("approve_price", "approve_terms_policy", "approve_privacy_policy", "approve_refunds_policy",
                  "commercial_gate_decision"):
            self.assertIn(a, actions)
        pricing_md = (self.outputs / "phase4_step1_pricing.md").read_text()
        self.assertIn("| Generic salon software | $40/month | ASSUMPTION |", pricing_md)
        self.assertIn("ASSUMPTION: groomers pay for tools", pricing_md)

    def test_founder_edits_are_rechecked(self):
        def tamper(n):
            if n == 6:  # just before the founder answers the first policy prompt
                path = self.outputs / policies.FILES["refunds"]
                path.write_text(path.read_text().replace("not legal advice", "official").replace(
                    "14 days", "[REFUND PERIOD]"))
        result = self.run_phase([PRICE, REVIEW, REVIEW, REVIEW],
                                ["a", *BUSINESS, "r", "a", "g", "a", "a"], on_prompt=tamper)
        self.assertEqual(result["outcome"], "go")
        out = "\n".join(self.out)
        self.assertIn("refunds: the 'not legal advice' notice was removed", out)
        self.assertIn("refunds: has unfilled placeholder text ([REFUND PERIOD])", out)
        self.assertIn("Please type one of: r, g, s", self.out)  # approve not offered while checks fail

    def test_fake_discount_cannot_be_approved(self):
        bad = {**PRICE, "headline": "Was $49, now $19. Limited time!"}
        result = self.run_phase([bad, PRICE, REVIEW], ["a", "r", "No fake discounts", "a", *BUSINESS, "a", "a"])
        self.assertEqual(result["outcome"], "go")
        self.assertIn("Please type one of: e, r, s", self.out)
        self.assertIn("No fake discounts", self.llm.calls[1])

    def test_price_that_waitlist_cannot_cover_pivots(self):
        expensive = {**PRICE, "monthly_costs": 5000}  # needs 264 customers, waitlist has 120
        result = self.run_phase([expensive, REVIEW, PRICE, REVIEW],
                                ["a", *BUSINESS, "a", "p", "Hosting should be nearly free", "a", "a", "a"])
        self.assertEqual(result["outcome"], "go")
        self.assertIn("Hosting should be nearly free", self.llm.calls[2])
        gates = [e for e in self.log() if e["action"] == "commercial_gate"]
        self.assertEqual([g["outcome"] for g in gates], ["pivot", "go"])
        self.assertIn("264 paying customers", gates[0]["reasons"][0])
        self.assertEqual(sum("Business or trading name" in p for p in self.founder.prompts), 1)

    def test_stop_on_pivot_writes_no_handoff(self):
        result = self.run_phase([{**PRICE, "monthly_costs": 5000}, REVIEW], ["a", *BUSINESS, "a", "s"])
        self.assertEqual(result["outcome"], "pivot")
        self.assertFalse((self.outputs / "phase4_step4_handoff.json").exists())

    def test_one_time_no_refunds_and_no_waitlist(self):
        handoff = {**HANDOFF, "evidence": {}}
        one_time = {**PRICE, "billing": "one_time", "price": 49, "refund_days": 0, "monthly_costs": 0}
        self.run_phase([one_time, REVIEW], ["a", *BUSINESS, "a", "a"], handoff=handoff)
        terms, refunds, privacy = self.read("terms"), self.read("refunds"), self.read("privacy")
        self.assertIn("nothing renews", terms)
        self.assertNotIn("## 3. Cancelling", terms)
        self.assertIn("not refundable", refunds)
        self.assertNotIn("waitlist", privacy.lower())

    def test_invalid_email_is_asked_again(self):
        self.run_phase([PRICE, REVIEW], ["a", "Ana", "not-an-email", "help@ana.example", "Egypt", "", "a", "a"])
        self.assertIn("help@ana.example", self.read("terms"))

    def test_reviewer_fix_points_must_be_acknowledged(self):
        self.run_phase([PRICE, REVIEW_FIX], ["a", *BUSINESS, "a", "a"])
        self.assertTrue(any("I have read the 1 points" in o for o in self.out))
        terms_ok = [e for e in self.log() if e["action"] == "approve_terms_policy"][0]
        self.assertEqual(terms_ok["reviewer_fix_points_acknowledged"], 1)

    def test_trick_prompt_in_feedback_is_refused(self):
        self.run_phase([PRICE, PRICE, REVIEW],
                       ["r", "add fake testimonials to the pricing page", "make it cheaper", "a", *BUSINESS, "a", "a"])
        self.assertTrue(any("can't help" in o for o in self.out))
        self.assertNotIn("testimonials", self.llm.calls[1])

    def test_missing_handoff_is_an_error(self):
        (self.outputs / "phase3_step5_handoff.json").unlink()
        with self.assertRaises(FileNotFoundError):
            CommercialPipeline(FakeLLM([]), outputs_dir=self.outputs, log_path=self.tmp / "l.json").run()


class UnitTest(unittest.TestCase):
    def test_pricing_maths_and_text(self):
        p = pricing.normalise({**PRICE, "billing": "yearly", "price": "120", "monthly_costs": 25})
        self.assertEqual(pricing.break_even(p), {"needed": 3, "unit": "paying customers",
                                                 "monthly_revenue_per_customer": 10.0})
        self.assertEqual(pricing.price_line(p), "$120 per year")
        self.assertIn("cancel any time", pricing.billing_note(p))
        self.assertEqual(pricing.money(9.5, "EGP"), "9.50 EGP")

    def test_pricing_validation(self):
        self.assertEqual(pricing.validate(pricing.normalise(PRICE)), [])
        for bad in ({"price": 0}, {"billing": "weekly"}, {"refund_days": 365}, {"includes": ["Save 50% today"]},
                    {"headline": "Guaranteed results"}):
            self.assertTrue(pricing.validate(pricing.normalise({**PRICE, **bad})), bad)

    def test_gate(self):
        be = {"needed": 3, "unit": "paying customers"}
        self.assertEqual(commercial_gate([], [], be, 120).outcome, "go")
        self.assertEqual(commercial_gate([], [], be, 2).outcome, "pivot")
        self.assertEqual(commercial_gate([], ["terms: x"], be, 120).outcome, "repeat")
        self.assertEqual(commercial_gate([], [], be, None).outcome, "go")

    def test_story_to_capability(self):
        self.assertEqual(policies.capability("As a groomer, I can add clients so that I don't forget them"), "add clients")
        self.assertEqual(policies.capability("As a groomer I can remove clients."), "remove clients")
        self.assertEqual(policies.capability("Export a CSV"), "Export a CSV")

    def test_markdown_links_are_not_placeholders(self):
        self.assertIsNone(policies.PLACEHOLDER.search("See the [Privacy policy](privacy.md)."))
        self.assertIsNotNone(policies.PLACEHOLDER.search("Contact [BUSINESS EMAIL]"))


if __name__ == "__main__":
    unittest.main()
