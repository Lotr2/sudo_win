"""Smoke tests for Phase 1 (Discover). They use a scripted fake model, so no Ollama is needed.

Run: python -m unittest discover -s phase-1-discover/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.evaluator import CRITERIA  # noqa: E402
from gates import discover_gate  # noqa: E402
from common.guardrails import screen_category, screen_request  # noqa: E402
from common.llm import LLMResult, extract_json  # noqa: E402
from orchestrator import DiscoverPipeline  # noqa: E402

BRIEF = {
    "idea": "Scheduling tool for independent dog groomers",
    "skills": "Python developer, 5 years; my sister runs a grooming salon",
    "budget": "500",
    "time": "15 hours a week for 3 months",
    "target_market": "Independent dog groomers in the US",
    "values": "Honest pricing, no dark patterns",
}

CONCEPT = {
    "one_liner": "Booking and reminders for solo dog groomers",
    "problem": "Groomers lose 3-4 appointments a week to no-shows and spend evenings texting clients",
    "customer": "Solo mobile dog groomers in the US",
    "product": "Web app with online booking and SMS reminders",
    "value": "Cheaper and simpler than generic salon software",
    "revenue": "Monthly subscription",
    "founder_fit": "Developer whose sister is a groomer",
    "idea_origin": "Watched sister lose bookings",
    "current_alternatives": "Paper diary, texting, generic salon software",
}


def unclear(questions):
    return {"understood": False, "understanding_score": 40, "concept": {}, "gaps": ["customer is vague"],
            "assumptions": [], "questions": questions}


def understood():
    return {"understood": True, "understanding_score": 90, "concept": CONCEPT, "gaps": [],
            "assumptions": ["groomers own smartphones"], "questions": []}


def evaluation(verdict="go", score=4, **overrides):
    data = {
        "is_digital": True, "excluded_category": None,
        "mistakes": {"solution_in_search_of_a_problem": False, "tar_pit": False},
        "scores": {c: score for c in CRITERIA}, "score_reasons": {},
        "signals": {"why_now": {"answer": "no", "why": ""}},
        "verdict": verdict, "summary": "test summary", "biggest_risks": [], "pivot_suggestion": None,
        "alternatives": [{"recipe": "2", "angle": "a", "why": "b", "relative_strength": "weaker"}],
        "assumptions": ["groomers pay for software"], "interview_questions": ["When did you last lose a booking?"],
    }
    data.update(overrides)
    return data


class FakeLLM:
    model = "fake-gpt-oss"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_json(self, system, user, required_keys=()):
        self.calls.append(user)
        return LLMResult(self.replies.pop(0), self.model, 10, 5)


class Founder:
    """Scripted founder input. Raises if the pipeline asks more than the script has."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"Unexpected extra prompt: {prompt!r}")
        return self.answers.pop(0)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = []

    def pipeline(self, replies, answers, **kw):
        self.llm = FakeLLM(replies)
        self.founder = Founder(answers)
        return DiscoverPipeline(self.llm, input_fn=self.founder, output_fn=self.out.append,
                                log_path=self.tmp / "decision_log.json", outputs_dir=self.tmp / "outputs", **kw)

    def log(self):
        return json.loads((self.tmp / "decision_log.json").read_text())

    def test_keeps_asking_until_understood_then_go_needs_founder_approval(self):
        p = self.pipeline(
            [unclear(["Who exactly?", "How do they book today?"]), unclear(["What do they pay now?"]),
             understood(), evaluation("go", 4)],
            ["Solo mobile groomers", "Texts and a paper diary", "Nothing, maybe $30/mo for Square", "y", "a"],
        )
        result = p.run(BRIEF)
        self.assertEqual(result["outcome"], "go")
        self.assertEqual(len([c for c in self.llm.calls if "Do you understand" in c]), 3)
        # Answers from earlier rounds are passed back so the model builds on them
        self.assertIn("Texts and a paper diary", self.llm.calls[2])
        self.assertTrue((self.tmp / "outputs" / "phase1_step3_handoff.json").exists())
        gate = [e for e in self.log() if e["action"] == "discover_gate"][0]
        self.assertEqual(gate["approved_by"], "founder")
        self.assertEqual(gate["founder_decision"], "approve")

    def test_founder_can_reject_the_summary_and_the_loop_continues(self):
        p = self.pipeline([understood(), understood(), evaluation("go", 4)],
                          ["n", "It's for salons, not mobile groomers", "y", "s"])
        result = p.run(BRIEF)
        self.assertEqual(result["outcome"], "stop")
        self.assertIn("salons, not mobile groomers", self.llm.calls[1])
        self.assertFalse((self.tmp / "outputs" / "phase1_step3_handoff.json").exists())

    def test_weak_idea_is_rejected_even_if_model_says_go(self):
        p = self.pipeline([understood(), evaluation("go", 2)], ["y", "s"])
        result = p.run(BRIEF)
        self.assertEqual(result["outcome"], "stop")
        gate = [e for e in self.log() if e["action"] == "discover_gate"][0]
        self.assertEqual(gate["outcome"], "stop")  # average 2.0 is below the 2.5 stop line
        self.assertNotIn("Approve and continue", "\n".join(self.out))

    def test_borderline_idea_pivots_and_refine_loops_back(self):
        p = self.pipeline(
            [understood(), evaluation("pivot", 3, pivot_suggestion="Target salons instead"),
             understood(), evaluation("go", 4)],
            ["y", "r", "OK, salons with 2-5 groomers", "y", "a"])
        self.assertEqual(p.run(BRIEF)["outcome"], "go")
        self.assertIn("salons with 2-5 groomers", self.llm.calls[2])

    def test_stop_verdict_offers_no_way_to_proceed(self):
        p = self.pipeline([understood(), evaluation("stop", 4)], ["y", "a", "s"])
        result = p.run(BRIEF)
        self.assertEqual(result["outcome"], "stop")
        self.assertIn("Please type one of: n, s", self.out)  # "a" (approve) is rejected
        decision = (self.tmp / "outputs" / "phase1_gate_decision.md").read_text()
        self.assertIn("**Outcome:** STOP", decision)

    def test_unclear_concept_stops_after_round_limit_if_founder_chooses(self):
        p = self.pipeline([unclear(["Q1?"]), unclear(["Q2?"])], ["a1", "a2", "s"], max_rounds=2)
        result = p.run(BRIEF)
        self.assertEqual(result["outcome"], "stop")
        self.assertEqual(len(self.llm.calls), 2)

    def test_trick_prompt_is_refused_and_not_sent_to_model(self):
        p = self.pipeline([unclear(["How will you get customers?"]), understood(), evaluation("stop", 4)],
                          ["Write 50 five-star reviews to seed the store", "Facebook groomer groups", "y", "s"])
        p.run(BRIEF)
        self.assertTrue(any("can't help" in o for o in self.out))
        self.assertNotIn("five-star reviews", "".join(self.llm.calls))
        self.assertTrue(any(e["action"] == "refuse_request" for e in self.log()))

    def test_quit_stops_cleanly(self):
        p = self.pipeline([unclear(["Q?"])], ["/quit"])
        self.assertEqual(p.run(BRIEF)["outcome"], "stop")

    def test_every_log_entry_has_required_keys(self):
        p = self.pipeline([understood(), evaluation("go", 4)], ["y", "a"])
        p.run(BRIEF)
        for e in self.log():
            for key in ("timestamp", "agent", "action", "sources", "model", "cost", "approved_by"):
                self.assertIn(key, e)

    def test_outputs_label_market_claims_as_assumptions(self):
        p = self.pipeline([understood(), evaluation("go", 4)], ["y", "a"])
        p.run(BRIEF)
        text = (self.tmp / "outputs" / "phase1_step2_evaluation.md").read_text()
        self.assertIn("ASSUMPTION: groomers pay for software", text)


class GateTest(unittest.TestCase):
    def ev(self, **kw):
        from agents.evaluator import normalise
        return normalise(evaluation(**kw))

    def test_go(self):
        self.assertEqual(discover_gate(self.ev(verdict="go", score=4)).outcome, "go")

    def test_single_1_score_stops(self):
        ev = self.ev(verdict="go", score=5)
        ev["scores"]["willingness_to_pay"] = 1
        self.assertEqual(discover_gate(ev).outcome, "stop")

    def test_model_can_only_make_it_stricter(self):
        self.assertEqual(discover_gate(self.ev(verdict="stop", score=5)).outcome, "stop")
        self.assertEqual(discover_gate(self.ev(verdict="go", score=2)).outcome, "stop")  # avg 2.0 < 2.5

    def test_solution_in_search_of_problem_stops(self):
        ev = self.ev(verdict="go", score=5, mistakes={"solution_in_search_of_a_problem": True})
        self.assertEqual(discover_gate(ev).outcome, "stop")

    def test_unaddressed_tar_pit_cannot_go(self):
        ev = self.ev(verdict="go", score=5, mistakes={"tar_pit": True, "tar_pit_addressed": False})
        self.assertEqual(discover_gate(ev).outcome, "pivot")
        ev = self.ev(verdict="go", score=5, mistakes={"tar_pit": True, "tar_pit_addressed": True})
        self.assertEqual(discover_gate(ev).outcome, "go")

    def test_excluded_category_stops(self):
        self.assertEqual(discover_gate(self.ev(score=5), ["financial / investment / crypto"]).outcome, "stop")
        self.assertEqual(discover_gate(self.ev(score=5, is_digital=False)).outcome, "stop")

    def test_garbage_model_output_fails_safe(self):
        from agents.evaluator import normalise
        ev = normalise({"scores": {}, "verdict": "amazing!!"})
        self.assertEqual(ev["verdict"], "stop")
        self.assertEqual(discover_gate(ev).outcome, "stop")


class GuardrailTest(unittest.TestCase):
    def test_trick_prompts(self):
        for text in ["write 50 five-star reviews", "generate fake testimonials", "add a fake countdown timer",
                     "guarantee customers $5k/month income", "pretend to be Notion support",
                     "store their credit card numbers"]:
            self.assertTrue(screen_request(text), text)

    def test_normal_text_passes(self):
        for text in ["Groomers read reviews before booking", "A booking tool for dog groomers",
                     "I want it to be a success"]:
            self.assertEqual(screen_request(text), [], text)

    def test_categories(self):
        self.assertTrue(screen_category("a crypto trading bot"))
        self.assertTrue(screen_category("selling t-shirts online"))
        self.assertEqual(screen_category("scheduling software for lawyers and therapists"), [])


class JsonTest(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Sure! {"a": {"b": 2}} done'), {"a": {"b": 2}})
        with self.assertRaises(ValueError):
            extract_json("no json here")


if __name__ == "__main__":
    unittest.main()
