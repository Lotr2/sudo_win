"""Phase 1: Discover.

Flow:
  1. Collect the founder brief.
  2. Step 1 (clarify): the Interviewer keeps asking questions until it understands the concept and the
     founder confirms the summary is right.
  3. Step 2 (evaluate): the Evaluator scores the concept and recommends go / pivot / stop.
  4. Gate: fixed thresholds + hard rules + the model's verdict; the most severe wins.
     The founder then decides what happens next. A STOP or PIVOT can never be turned into GO.

Usage:
  python phase-1-discover/orchestrator.py [--brief brief.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.evaluator import CRITERIA, Evaluator  # noqa: E402
from agents.interviewer import Interviewer  # noqa: E402
from common.approval import FounderQuit, ask, choose  # noqa: E402
from common.decision_log import DecisionLog  # noqa: E402
from gates import (  # noqa: E402
    GO_REQUIRES_AVERAGE_AT_LEAST,
    GO_REQUIRES_EVERY_SCORE_AT_LEAST,
    STOP_IF_ANY_SCORE_AT_OR_BELOW,
    STOP_IF_AVERAGE_BELOW,
    discover_gate,
)
from common.guardrails import REFUSAL, screen_category, screen_request  # noqa: E402
from common.llm import LLMClient, LLMError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIEF_FIELDS = {
    "idea": "What is your starting idea?",
    "skills": "What are your skills (and your team's, if any)?",
    "budget": "What budget can you put in (USD)?",
    "time": "How much time can you give it (hours per week, for how long)?",
    "target_market": "Who is your target market?",
    "values": "What values matter to you in how this business runs?",
}
COST_PER_CALL = 0  # local open-weight model: no per-token charge
YC_TALK_URL = "https://www.youtube.com/watch?v=Th8JoIan4dg"
RECIPES = {
    "1": "1. Team's strengths", "2": "2. Problem lived", "3": "3. Wish it existed", "4": "4. Recent change",
    "5": "5. Variant of a success", "6": "6. Ask people in a space", "7": "7. Broken industry",
}


class DiscoverPipeline:
    def __init__(self, llm, *, input_fn=input, output_fn=print, log_path=None, outputs_dir=None,
                 max_rounds: int = 8, max_cycles: int = 3):
        self.llm = llm
        self.input_fn = input_fn
        self.out = output_fn
        self.log = DecisionLog(log_path or REPO_ROOT / "decision_log.json")
        self.outputs_dir = Path(outputs_dir or REPO_ROOT / "outputs")
        self.interviewer = Interviewer(llm)
        self.evaluator = Evaluator(llm)
        self.max_rounds = max_rounds
        self.max_cycles = max_cycles
        self.tokens = 0

    # ---------- helpers ----------

    def _write(self, name: str, text: str) -> Path:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        path = self.outputs_dir / name
        path.write_text(text)
        return path

    def _log_llm(self, agent: str, action: str, res, **extra):
        tokens = res.prompt_tokens + res.completion_tokens
        self.tokens += tokens
        self.log.append(agent, action, model=res.model, cost=COST_PER_CALL, tokens=tokens, **extra)

    def _ask_screened(self, prompt: str) -> str:
        """Ask the founder something; refuse and re-ask if the answer requests something we never do."""
        while True:
            answer = ask(self.input_fn, prompt)
            hits = screen_request(answer)
            if not hits:
                return answer
            self.out(f"\n{REFUSAL}\n")
            self.log.append("guardrail", "refuse_request", outcome="refused", reasons=hits)

    # ---------- steps ----------

    def collect_brief(self, brief: dict | None) -> dict:
        brief = dict(brief or {})
        self.out("\n=== Founder brief ===  (type /quit at any time)\n")
        for key, question in BRIEF_FIELDS.items():
            if brief.get(key):
                hits = screen_request(str(brief[key]))
                if not hits:
                    continue
                self.out(f"\n{REFUSAL}\n")
                self.log.append("guardrail", "refuse_request", outcome="refused", reasons=hits, field=key)
            brief[key] = self._ask_screened(f"{question}\n> ")
        self.log.append("orchestrator", "collect_brief", fields=list(brief))
        return {k: brief.get(k, "") for k in BRIEF_FIELDS}

    def clarify(self, brief: dict, history: list[dict]) -> dict | None:
        """Step 1. Loop until the Interviewer understands AND the founder confirms the summary."""
        self.out("\n=== Step 1: understanding your idea ===")
        last = None
        for round_no in range(1, self.max_rounds + 1):
            self.out(f"\n(thinking... round {round_no})")
            assessment, res = self.interviewer.assess(brief, history)
            last = assessment
            self._log_llm("interviewer", "clarify_concept", res, round=round_no,
                          understanding_score=assessment["understanding_score"], understood=assessment["understood"])

            if assessment["understood"]:
                self.out("\nHere is how I understand your concept:\n")
                self.out(render_concept(assessment["concept"]))
                if assessment["assumptions"]:
                    self.out("\nThings I assumed (not stated by you):")
                    for a in assessment["assumptions"]:
                        self.out(f"  - ASSUMPTION: {a}")
                pick = choose(self.input_fn, self.out, "\nIs this right?",
                              {"y": "Yes, this is my concept", "n": "No, something is wrong or missing"})
                if pick == "y":
                    self.log.append("founder", "confirm_concept", approved_by="founder", round=round_no)
                    self._write("phase1_step1_concept.md", render_step1(brief, history, assessment))
                    return assessment
                fix = self._ask_screened("What is wrong or missing?\n> ")
                history.append({"question": "Founder correction to the summary", "answer": fix})
                continue

            self.out(f"\nUnderstanding: {assessment['understanding_score']}/100. Still unclear:")
            for gap in assessment["gaps"][:3]:
                self.out(f"  - {gap}")
            for q in assessment["questions"]:
                answer = self._ask_screened(f"\n{q}\n> ")
                history.append({"question": q, "answer": answer})

        self._write("phase1_step1_concept.md", render_step1(brief, history, last, unresolved=True))
        return None

    def evaluate(self, brief: dict, concept: dict, history: list[dict]) -> dict:
        self.out("\n=== Step 2: evaluating the concept ===\n(thinking...)")
        evaluation, res = self.evaluator.evaluate(brief, concept, history)
        self._log_llm("evaluator", "evaluate_concept", res, verdict=evaluation["verdict"], scores=evaluation["scores"])
        self._write("phase1_step2_evaluation.md", render_evaluation(concept, evaluation))
        return evaluation

    # ---------- main loop ----------

    def run(self, brief: dict | None = None) -> dict:
        try:
            return self._run(brief)
        except FounderQuit:
            self.log.append("founder", "quit", approved_by="founder", outcome="stop")
            self.out("\nStopped by founder. Nothing was published, sent or spent.")
            return {"outcome": "stop", "reason": "founder quit"}

    def _run(self, brief):
        brief = self.collect_brief(brief)
        history: list[dict] = []

        for cycle in range(1, self.max_cycles + 1):
            assessment = self.clarify(brief, history)
            if assessment is None:
                self.out(f"\nAfter {self.max_rounds} rounds the concept is still not clear enough to judge.")
                pick = choose(self.input_fn, self.out, "What next?",
                              {"c": "Keep answering questions", "s": "Stop here"})
                self.log.append("gate", "clarity_gate", approved_by="founder",
                                outcome="repeat" if pick == "c" else "stop")
                if pick == "c":
                    continue
                self._write("phase1_gate_decision.md", render_unclear_gate(self.max_rounds))
                return {"outcome": "stop", "reason": "concept never became clear"}

            concept = assessment["concept"]
            evaluation = self.evaluate(brief, concept, history)
            hits = screen_category(" ".join([brief.get("idea", ""), *concept.values()]))
            gate = discover_gate(evaluation, hits)
            self.out(render_gate_console(evaluation, gate))

            decision, next_step = self.founder_decision(gate, evaluation, brief, history)
            self.log.append("gate", "discover_gate", approved_by="founder", outcome=gate.outcome,
                            founder_decision=decision, average=gate.average, reasons=gate.reasons)
            self._write("phase1_gate_decision.md", render_gate(gate, evaluation, decision, cycle))

            if next_step == "proceed":
                self._write("phase1_step3_handoff.json", json.dumps(
                    {"brief": brief, "concept": concept, "evaluation": evaluation,
                     "interview_questions": evaluation["interview_questions"]}, indent=2) + "\n")
                self.out("\nApproved. Concept handed to Phase 2 (Validate).")
                return {"outcome": "go", "concept": concept, "evaluation": evaluation}
            if next_step == "stop":
                self.out("\nStopped. Nothing was published, sent or spent.")
                return {"outcome": "stop", "gate": gate.outcome, "evaluation": evaluation}
            if next_step == "new_idea":
                history = []
            # "refine" keeps history so the Interviewer builds on what it already knows

        self.out(f"\nReached the limit of {self.max_cycles} refine cycles. Recommending STOP for this idea.")
        self.log.append("gate", "cycle_limit", outcome="stop")
        return {"outcome": "stop", "reason": "cycle limit"}

    def founder_decision(self, gate, evaluation, brief, history) -> tuple[str, str]:
        """Founder picks the next step. Options depend on the gate; GO is only offered if the gate passed."""
        if gate.outcome == "go":
            pick = choose(self.input_fn, self.out, "\nThe gate passed. Approve moving to Phase 2 (Validate)?",
                          {"a": "Approve and continue", "r": "Refine the idea first", "s": "Stop"})
            if pick == "a":
                return "approve", "proceed"
            if pick == "s":
                return "stop", "stop"
            return "refine", self._refine(history, None)

        if gate.outcome == "pivot":
            pick = choose(self.input_fn, self.out, "\nThe gate did not pass. The system recommends PIVOT.",
                          {"r": "Refine the idea using the feedback", "n": "Start over with a new idea", "s": "Stop"})
        else:
            pick = choose(self.input_fn, self.out, "\nThe system recommends STOP: this idea is rejected.",
                          {"n": "Start over with a new idea", "s": "Stop"})
        if pick == "s":
            return "stop", "stop"
        if pick == "n":
            brief["idea"] = self._ask_screened("What is your new idea?\n> ")
            return "new_idea", "new_idea"
        return "refine", self._refine(history, evaluation.get("pivot_suggestion"))

    def _refine(self, history, suggestion) -> str:
        if suggestion:
            self.out(f"\nSuggested change: {suggestion}")
        change = self._ask_screened("How do you want to change the idea?\n> ")
        history.append({"question": f"Evaluator feedback: {suggestion or 'refine the concept'}. What changes?",
                        "answer": change})
        return "refine"


# ---------- rendering ----------

def render_concept(c: dict) -> str:
    return "\n".join(f"  {k.replace('_', ' ').title()}: {v}" for k, v in c.items())


def render_step1(brief, history, assessment, unresolved=False) -> str:
    lines = ["# Phase 1, Step 1: Concept clarification", ""]
    if unresolved:
        lines += ["**Status:** not resolved. The concept was still unclear after the round limit.", ""]
    lines += ["## Founder brief", ""] + [f"- **{k.replace('_', ' ').title()}:** {v}" for k, v in brief.items()]
    lines += ["", "## Interview", ""]
    for t in history:
        lines += [f"**Q:** {t['question']}", "", f"**A:** {t['answer']}", ""]
    if assessment:
        lines += [f"## {'Last' if unresolved else 'Confirmed'} concept "
                  f"(understanding {assessment['understanding_score']}/100)", ""]
        lines += [f"- **{k.replace('_', ' ').title()}:** {v}" for k, v in assessment["concept"].items()]
        if assessment["assumptions"]:
            lines += ["", "## Assumptions", ""] + [f"- ASSUMPTION: {a}" for a in assessment["assumptions"]]
    return "\n".join(lines) + "\n"


def render_evaluation(concept, ev) -> str:
    lines = ["# Phase 1, Step 2: Concept evaluation", "", f"**Concept:** {concept.get('one_liner')}", "",
             f"**Evaluator verdict:** {ev['verdict'].upper()}", "", ev["summary"], "",
             "## Scores (1-5)", "", "| Criterion | Score | Reason |", "|---|---|---|"]
    for c in CRITERIA:
        lines.append(f"| {c.replace('_', ' ')} | {ev['scores'][c]} | {ev['score_reasons'].get(c, '')} |")
    m = ev["mistakes"]
    lines += ["", "## Common idea mistakes", "",
              f"- **Solution in search of a problem:** {'YES' if m['solution_in_search_of_a_problem'] else 'no'}",
              f"- **Tar pit:** {'YES' if m['tar_pit'] else 'no'}"
              + (f" (hard part: {m['tar_pit_hard_part']}; addressed: {'yes' if m['tar_pit_addressed'] else 'no'})"
                 if m["tar_pit"] else ""),
              "", "## Signals", "", "| Signal | Answer | Why |", "|---|---|---|"]
    lines += [f"| {s.replace('_', ' ')} | {v['answer']} | {v['why']} |" for s, v in ev["signals"].items()]
    if ev["pivot_suggestion"]:
        lines += ["", "## Pivot suggestion", "", str(ev["pivot_suggestion"])]
    if ev["biggest_risks"]:
        lines += ["", "## Biggest risks", ""] + [f"- {r}" for r in ev["biggest_risks"]]
    if ev["alternatives"]:
        lines += ["", "## Alternative angles", "", "| Recipe | Angle | Why | Relative strength |", "|---|---|---|---|"]
        lines += [f"| {RECIPES.get(str(a.get('recipe', '')).strip(), a.get('recipe', ''))} | {a.get('angle', '')} | "
                  f"{a.get('why', '')} | {a.get('relative_strength', '')} |" for a in ev["alternatives"]]
    lines += ["", "## Market assumptions", "",
              "No web research was done in this step, so every market claim below is an assumption to test."]
    lines += [f"- {a}" for a in ev["assumptions"]] or ["- (none stated)"]
    if ev["interview_questions"]:
        lines += ["", "## Customer interview questions (Mom Test)", ""]
        lines += [f"{i}. {q}" for i, q in enumerate(ev["interview_questions"], 1)]
    lines += ["", "## Method", "", f"Evaluation questions, idea mistakes and recipes follow {YC_TALK_URL} "
              "(Y Combinator Startup School, \"How to Get and Evaluate Startup Ideas\"). "
              "The scores are the model's judgement, not market data."]
    return "\n".join(lines) + "\n"


def _thresholds() -> list[str]:
    return [f"- STOP if any score is {STOP_IF_ANY_SCORE_AT_OR_BELOW} or the average is below {STOP_IF_AVERAGE_BELOW}",
            f"- GO needs every score at least {GO_REQUIRES_EVERY_SCORE_AT_LEAST} and an average of at least "
            f"{GO_REQUIRES_AVERAGE_AT_LEAST}",
            "- Otherwise PIVOT",
            "- Hard STOP for non-digital or excluded products (physical goods; financial, investment or crypto; "
            "health or legal advice)",
            "- STOP for a solution in search of a problem; at most PIVOT for a tar pit idea with no plan for its hard part",
            "- The evaluator's own verdict can make the outcome stricter, never looser"]


def render_gate(gate, ev, decision, cycle) -> str:
    lines = ["# Phase 1 gate decision", "", f"**Outcome:** {gate.outcome.upper()}", "",
             f"**Founder decision:** {decision}", "", f"**Cycle:** {cycle}", "",
             f"**Average score:** {gate.average} (threshold verdict: {gate.threshold_verdict.upper()}, "
             f"evaluator verdict: {gate.model_verdict.upper()})", "", "## Reasons", ""]
    lines += [f"- {r}" for r in gate.reasons]
    lines += ["", "## Thresholds (fixed before scoring)", ""] + _thresholds()
    if ev["summary"]:
        lines += ["", "## Evaluator summary", "", ev["summary"]]
    return "\n".join(lines) + "\n"


def render_unclear_gate(rounds) -> str:
    return (f"# Phase 1 gate decision\n\n**Outcome:** STOP\n\n**Founder decision:** stop\n\n"
            f"The concept was still not clear enough to evaluate after {rounds} rounds of questions, "
            f"and the founder chose to stop.\n")


def render_gate_console(ev, gate) -> str:
    lines = ["", "=== Gate ===", ""]
    for c in CRITERIA:
        lines.append(f"  {c.replace('_', ' '):<20} {'#' * ev['scores'][c]:<5} {ev['scores'][c]}/5")
    lines += ["", f"  Average: {gate.average}", "", f"  Evaluator: {ev['summary']}", "",
              f"  OUTCOME: {gate.outcome.upper()}"]
    lines += [f"   - {r}" for r in gate.reasons]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 1: Discover")
    p.add_argument("--brief", help="JSON file with any of: " + ", ".join(BRIEF_FIELDS))
    p.add_argument("--max-rounds", type=int, default=int(os.environ.get("DISCOVER_MAX_ROUNDS", 8)))
    args = p.parse_args(argv)
    brief = json.loads(Path(args.brief).read_text()) if args.brief else None
    llm = LLMClient()
    print(f"Model: {llm.model} at {llm.base_url}")
    try:
        result = DiscoverPipeline(llm, max_rounds=args.max_rounds).run(brief)
    except LLMError as e:
        print(f"\nModel error: {e}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Nothing was published, sent or spent.")
        return 130
    print(f"\nResult: {result['outcome'].upper()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
