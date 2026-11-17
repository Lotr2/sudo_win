"""Smoke tests for Phase 2 (Validate). A scripted fake model stands in for gpt-oss, so no server is needed.

Run: python -m unittest discover -s phase-2-validate/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.guardrails import screen_copy  # noqa: E402
from common.llm import LLMResult  # noqa: E402
from gates import PlanTampered, demand_gate, plan_hash, verify_lock  # noqa: E402
from landing_page import approved_page, make_handler, publish, render  # noqa: E402
from orchestrator import PLAN_FILE, ValidatePipeline  # noqa: E402
from waitlist import Waitlist  # noqa: E402

HANDOFF = {
    "brief": {"idea": "Booking for dog groomers", "skills": "Python", "budget": "500", "time": "15h/wk",
              "target_market": "US groomers", "values": "honesty"},
    "concept": {"one_liner": "Booking and reminders for solo dog groomers"},
    "evaluation": {"summary": "Worth testing."},
}

CANVAS = {
    "problem": ["No-shows", "Evenings spent texting"], "existing_alternatives": "Paper diary",
    "customer_segments": "Dog groomers", "early_adopters": "Solo mobile groomers in Texas",
    "unique_value_proposition": "Fewer no-shows without the admin", "high_level_concept": "",
    "solution": ["Online booking", "SMS reminders"], "channels": ["Groomer Facebook groups"],
    "revenue_streams": "$19/month", "cost_structure": "Hosting, SMS", "key_metrics": ["Weekly bookings"],
    "unfair_advantage": "", "riskiest_assumptions": [{"assumption": "Groomers want this", "why_risky": "x",
                                                     "how_to_test": "waitlist"}],
    "assumptions": ["groomers lose 3 bookings a week"],
}

PLAN = {"hypothesis": "h", "duration_days": 14, "min_visitors": 200, "target_signups": 100,
        "stop_below_signups": 30, "assumed_conversion_rate": 0.1,
        "channels": [{"where": "FB groups", "how": "post", "expected_visitors": 300}], "rationale": "r",
        "assumptions": ["10% conversion"]}

COPY = {"product_name": "Groomly", "headline": "Stop losing bookings to no-shows",
        "subheadline": "Booking and reminders for solo groomers", "problem_points": ["No-shows"],
        "how_it_works": ["Share your link"], "who_its_for": "Solo groomers", "cta_label": "Join the waitlist",
        "faq": [{"q": "When?", "a": "We are still checking demand."}]}


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
        self.out = []
        (self.tmp / "outputs").mkdir()
        (self.tmp / "outputs" / "phase1_step3_handoff.json").write_text(json.dumps(HANDOFF))

    def pipe(self, replies=(), answers=()):
        self.llm = FakeLLM(replies)
        return ValidatePipeline(self.llm, input_fn=Founder(answers), output_fn=self.out.append,
                                log_path=self.tmp / "log.json", outputs_dir=self.tmp / "outputs",
                                runtime_dir=self.tmp / "runtime")

    def log(self):
        return json.loads((self.tmp / "log.json").read_text())

    def run_plan(self):
        p = self.pipe([CANVAS, PLAN, COPY], ["a", "a", "p"])
        self.assertEqual(p.plan()["outcome"], "started")
        return p


class PlanTest(Base):
    def test_full_plan_publishes_only_after_approvals(self):
        self.run_plan()
        actions = [e["action"] for e in self.log()]
        for a in ("approve_canvas", "approve_demand_test_plan", "publish_landing_page"):
            self.assertIn(a, actions)
        self.assertTrue(all(e["approved_by"] == "founder" for e in self.log() if e["agent"] == "founder"))
        locked = json.loads((self.tmp / "outputs" / PLAN_FILE).read_text())
        self.assertIsNotNone(locked["started_at"])
        self.assertIn("Groomly", approved_page(self.tmp / "runtime" / "site"))
        canvas_md = (self.tmp / "outputs" / "phase2_step1_lean_canvas.md").read_text()
        self.assertIn("ASSUMPTION: groomers lose 3 bookings a week", canvas_md)

    def test_stopping_at_page_publishes_nothing(self):
        p = self.pipe([CANVAS, PLAN, COPY], ["a", "a", "s"])
        self.assertEqual(p.plan()["outcome"], "stop")
        self.assertFalse((self.tmp / "runtime" / "site" / "index.html").exists())

    def test_founder_can_edit_a_canvas_box(self):
        p = self.pipe([CANVAS, PLAN, COPY], ["e", "4", "Mobile groomers in Austin", "a", "a", "p"])
        p.plan()
        state = json.loads((self.tmp / "runtime" / "phase2_state.json").read_text())
        self.assertEqual(state["canvas"]["early_adopters"], "Mobile groomers in Austin")

    def test_invalid_thresholds_cannot_be_approved(self):
        # Founder edits stop line above target: approve must not be offered until fixed
        p = self.pipe([CANVAS, PLAN, COPY], ["a", "e", "", "", "", "150", "a", "e", "", "", "", "20", "a", "p"])
        p.plan()
        self.assertTrue(any("must be at least 0 and lower" in o for o in self.out))
        self.assertIn("Please type one of: e, r, s", self.out)
        self.assertEqual(json.loads((self.tmp / "outputs" / PLAN_FILE).read_text())["plan"]["stop_below_signups"], 20)

    def test_dishonest_copy_is_regenerated_and_never_shown(self):
        bad = dict(COPY, subheadline="Join 500+ groomers. Only 10 spots left!")
        p = self.pipe([CANVAS, PLAN, bad, COPY], ["a", "a", "p"])
        p.plan()
        self.assertNotIn("500+", "".join(self.out))
        self.assertTrue(any(e["action"] == "reject_landing_copy" for e in self.log()))
        self.assertIn("rejected", self.llm.calls[-1])

    def test_copy_that_never_complies_is_not_published(self):
        bad = dict(COPY, headline="Guaranteed to double your income")
        p = self.pipe([CANVAS, PLAN, bad, bad, bad], ["a", "a", "s"])
        self.assertEqual(p.plan()["outcome"], "stop")
        self.assertFalse((self.tmp / "runtime" / "site" / "index.html").exists())

    def test_missing_phase1_handoff_is_an_error(self):
        (self.tmp / "outputs" / "phase1_step3_handoff.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.pipe().plan()


class GateTest(Base):
    def gate(self, signups, visitors, answers, days_later=15):
        later = datetime.now(timezone.utc) + timedelta(days=days_later)
        ev = {"signups": signups, "visitors": visitors, "source": "test host", "verified": False}
        return self.pipe(answers=answers).gate(ev, now=later)

    def test_problem_statement_example_stops(self):
        self.run_plan()  # target 100, stop line 30
        self.assertEqual(self.gate(12, 400, ["s"])["outcome"], "stop")
        self.assertNotIn("Approve: move to Phase 3", "\n".join(self.out))
        md = (self.tmp / "outputs" / "phase2_gate_demand_test.md").read_text()
        self.assertIn("**Outcome:** STOP", md)

    def test_go_needs_founder_approval_for_handoff(self):
        self.run_plan()
        self.assertEqual(self.gate(120, 400, ["s"])["decision"], "stop")
        self.assertFalse((self.tmp / "outputs" / "phase2_step4_handoff.json").exists())
        self.assertEqual(self.gate(120, 400, ["a"])["decision"], "approve")
        handoff = json.loads((self.tmp / "outputs" / "phase2_step4_handoff.json").read_text())
        self.assertEqual(handoff["canvas"]["early_adopters"], "Solo mobile groomers in Texas")

    def test_pivot_between_lines(self):
        self.run_plan()
        self.assertEqual(self.gate(50, 400, ["s"])["outcome"], "pivot")

    def test_low_traffic_is_no_conclusion(self):
        self.run_plan()
        self.assertEqual(self.gate(5, 50, ["s"])["outcome"], "repeat")

    def test_no_early_stop_while_test_running(self):
        self.run_plan()
        self.assertEqual(self.gate(5, 400, ["k"], days_later=3)["outcome"], "repeat")

    def test_moving_the_goalposts_is_refused(self):
        self.run_plan()
        path = self.tmp / "outputs" / PLAN_FILE
        locked = json.loads(path.read_text())
        locked["plan"]["stop_below_signups"] = 5
        locked["sha256"] = plan_hash(locked["plan"])  # even with a recomputed hash
        path.write_text(json.dumps(locked))
        with self.assertRaises(PlanTampered):
            self.gate(12, 400, ["s"])

    def test_gate_rules(self):
        locked = {"plan": PLAN, "locked_at": "2026-01-01T00:00:00Z"}
        later = datetime(2026, 2, 1, tzinfo=timezone.utc)
        ev = lambda s, v: {"signups": s, "visitors": v, "verified": True}  # noqa: E731
        self.assertEqual(demand_gate(locked, ev(100, 10), later).outcome, "go")
        self.assertEqual(demand_gate(locked, ev(29, 300), later).outcome, "stop")
        self.assertEqual(demand_gate(locked, ev(30, 300), later).outcome, "pivot")
        with self.assertRaises(PlanTampered):
            verify_lock({"plan": PLAN, "sha256": plan_hash(PLAN)}, None)


class LandingPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_model_text_is_escaped_and_notice_is_always_there(self):
        page = render(dict(COPY, headline='<script>alert(1)</script>'))
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("is not available yet", page)
        self.assertIn("There is nothing to buy", page)

    def test_unapproved_or_edited_page_is_not_served(self):
        site = self.tmp / "site"
        with self.assertRaises(PermissionError):
            approved_page(site)
        publish(site, render(COPY))
        (site / "index.html").write_text("<p>Join 10,000 happy users</p>")
        with self.assertRaises(PermissionError):
            approved_page(site)

    def test_server_counts_visitors_and_unique_signups(self):
        from http.server import ThreadingHTTPServer
        site, wl = self.tmp / "site", Waitlist(self.tmp / "wl")
        publish(site, render(COPY))
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(site, wl))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            self.assertIn("Groomly", urllib.request.urlopen(base + "/").read().decode())
            for body in (b"email=a%40b.com&website=", b"email=A%40B.com&website=", b"email=bot%40x.com&website=spam",
                         b"email=not-an-email&website="):
                urllib.request.urlopen(urllib.request.Request(base + "/join", data=body))
        finally:
            server.shutdown()
        self.assertEqual(wl.counts(), {"visitors": 1, "signups": 1})


class CopyGuardrailTest(unittest.TestCase):
    def test_flags_dishonest_copy(self):
        for text in ["Join 500+ founders", "Trusted by 2,000 teams", "Only 5 spots left", "Limited time offer",
                     "Guaranteed results", '"This changed my life" - Sarah', "Rated 5 stars", "Hurry!"]:
            self.assertTrue(screen_copy(text), text)

    def test_allows_honest_copy(self):
        for text in [COPY["headline"], COPY["subheadline"], "We are still checking demand; no date yet.",
                     "Join the waitlist", "Built for 1-3 person grooming businesses"]:
            self.assertEqual(screen_copy(text), [], text)


if __name__ == "__main__":
    unittest.main()
