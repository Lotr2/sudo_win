"""Phase 4: Commercial setup. Set the price and draft the policies. No payments of any kind.

  Step 1  Pricing analyst proposes one plan -> founder approves, edits the numbers, or asks for changes.
  Step 2  Founder enters business details (name, contact email, jurisdiction).
  Step 3  Terms, privacy and refund policies are filled in from templates by code, checked by code, and reviewed
          by the policy reviewer. The founder can edit the files and re-check, then approves.
  Gate    Pricing + policy checks + break-even vs. waitlist -> go / repeat / pivot -> founder decides.

Usage:
  python phase-4-commercial-setup/orchestrator.py [--handoff outputs/phase3_step5_handoff.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import policies  # noqa: E402
import pricing  # noqa: E402
from agents.policy_reviewer import PolicyReviewer  # noqa: E402
from agents.pricing_analyst import PricingAnalyst  # noqa: E402
from common.approval import FounderQuit, ask, choose  # noqa: E402
from common.decision_log import DecisionLog  # noqa: E402
from common.guardrails import REFUSAL, screen_request  # noqa: E402
from common.llm import LLMClient, LLMError  # noqa: E402
from gates import commercial_gate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_PER_CALL = 0  # local open-weight model
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class CommercialPipeline:
    def __init__(self, llm, *, input_fn=input, output_fn=print, log_path=None, outputs_dir=None, today=None):
        self.llm = llm
        self.input_fn = input_fn
        self.out = output_fn
        self.log = DecisionLog(log_path or REPO_ROOT / "decision_log.json")
        self.outputs = Path(outputs_dir or REPO_ROOT / "outputs")
        self.today = today or date.today().isoformat()

    def _write(self, name: str, text: str) -> Path:
        self.outputs.mkdir(parents=True, exist_ok=True)
        (self.outputs / name).write_text(text)
        return self.outputs / name

    def _log_llm(self, agent, action, res, **extra):
        self.log.append(agent, action, model=res.model, cost=COST_PER_CALL,
                        tokens=res.prompt_tokens + res.completion_tokens, **extra)

    def _ask(self, prompt: str, allow_empty: bool = False) -> str:
        while True:
            answer = ask(self.input_fn, prompt, allow_empty=allow_empty)
            hits = screen_request(answer)
            if not hits:
                return answer
            self.out(f"\n{REFUSAL}\n")
            self.log.append("guardrail", "refuse_request", outcome="refused", reasons=hits)

    # ---------- main ----------

    def run(self, handoff_path: Path | None = None) -> dict:
        try:
            return self._run(Path(handoff_path or self.outputs / "phase3_step5_handoff.json"))
        except FounderQuit:
            self.log.append("founder", "quit", approved_by="founder", outcome="stop")
            self.out("\nStopped by founder. Nothing was published, sent or spent.")
            return {"outcome": "stop"}

    def _run(self, handoff_path: Path) -> dict:
        if not handoff_path.exists():
            raise FileNotFoundError(f"{handoff_path} not found. Phase 3 must end with a founder-approved MVP first.")
        handoff = json.loads(handoff_path.read_text())
        self.log.append("orchestrator", "load_phase3_handoff", source_file=handoff_path.name)
        signups = (handoff.get("evidence") or {}).get("signups")
        business = None
        plan = None
        feedback = None

        while True:
            plan = self.pricing_step(handoff, signups, previous=plan, feedback=feedback)
            if plan is None:
                return {"outcome": "stop", "step": "pricing"}
            business = business or self.business_step()
            facts = policies.facts_from(handoff, plan, business)
            texts = self.policy_step(facts)
            if texts is None:
                return {"outcome": "stop", "step": "policies"}

            gate = commercial_gate(pricing.validate(plan), policies.check_policies(texts, facts),
                                   pricing.break_even(plan), signups)
            self.out(render_gate_console(gate))
            self.log.append("gate", "commercial_gate", outcome=gate.outcome, reasons=gate.reasons)
            options = {
                "go": {"a": "Approve the price and policies: move to Phase 5 (Launch)", "s": "Stop"},
                "pivot": {"p": "Change the price or costs", "b": "Go back to Phase 2 (Validate)", "s": "Stop"},
                "repeat": {"p": "Change the price", "s": "Stop"},
            }[gate.outcome]
            pick = choose(self.input_fn, self.out, f"\nOutcome: {gate.outcome.upper()}. What next?", options)
            decision = {"a": "approve", "p": "change_price", "b": "back_to_validate", "s": "stop"}[pick]
            self.log.append("founder", "commercial_gate_decision", approved_by="founder", outcome=gate.outcome,
                            founder_decision=decision)
            self._write("phase4_gate_commercial.md", render_gate_md(gate, plan, decision))
            if decision == "change_price":
                feedback = self._ask("What should change about the price or costs?\n> ")
                continue
            if decision == "approve":
                self._write("phase4_step4_handoff.json", json.dumps({
                    **handoff, "pricing": plan, "price_line": pricing.price_line(plan),
                    "billing_note": pricing.billing_note(plan), "refund_note": pricing.refund_note(plan),
                    "business": business, "policies": policies.FILES,
                }, indent=2) + "\n")
                self.out("\nApproved. Price and policies handed to Phase 5 (Launch). No payments are set up.")
                return {"outcome": "go", "pricing": plan}
            if decision == "back_to_validate":
                self.out("\nRun Phase 2 again: python phase-2-validate/orchestrator.py plan")
            return {"outcome": gate.outcome, "decision": decision}

    # ---------- step 1: pricing ----------

    def pricing_step(self, handoff, signups, previous=None, feedback=None):
        self.out("\n=== Step 1: pricing ===\n(thinking...)")
        analyst = PricingAnalyst(self.llm)
        plan, res = analyst.propose(handoff, previous=previous, feedback=feedback)
        self._log_llm("pricing_analyst", "propose_price", res, price=plan["price"], billing=plan["billing"])
        while True:
            problems = pricing.validate(plan)
            self.out(render_pricing_console(plan, signups))
            for p in problems:
                self.out(f"  ! {p}")
            options = {"e": "Edit the numbers", "r": "Change it with my feedback", "s": "Stop"}
            if not problems:
                options = {"a": "Approve this price", **options}
            pick = choose(self.input_fn, self.out, "\nReview the price (nothing is charged in this system):", options)
            if pick == "s":
                self.log.append("founder", "reject_price", approved_by="founder", outcome="stop")
                return None
            if pick == "a":
                self.log.append("founder", "approve_price", approved_by="founder", price=plan["price"],
                                billing=plan["billing"], refund_days=plan["refund_days"])
                self._write("phase4_step1_pricing.md", render_pricing_md(plan, signups))
                return plan
            if pick == "e":
                for key, cast in (("price", float), ("billing", str), ("refund_days", int), ("monthly_costs", float)):
                    hint = f" ({'/'.join(pricing.BILLING)})" if key == "billing" else ""
                    v = ask(self.input_fn, f"{key.replace('_', ' ')}{hint} [{plan[key]}]: ", allow_empty=True)
                    if v:
                        try:
                            plan[key] = cast(v) if key != "billing" else v.lower()
                        except ValueError:
                            self.out(f"  ! '{v}' is not a valid {key}; kept {plan[key]}")
                self.log.append("founder", "edit_price", approved_by="founder")
                continue
            feedback = self._ask("What should change?\n> ")
            self.out("(thinking...)")
            plan, res = analyst.propose(handoff, feedback=feedback, previous=plan)
            self._log_llm("pricing_analyst", "revise_price", res, price=plan["price"], billing=plan["billing"])

    # ---------- step 2: business details ----------

    def business_step(self) -> dict:
        self.out("\n=== Step 2: business details for the policies ===\n"
                 "(These appear in the public policies. Use a support address, not a personal one, if you prefer.)")
        name = self._ask("Business or trading name (or your own name):\n> ")
        while True:
            email = self._ask("Contact email for customers:\n> ")
            if EMAIL_RE.match(email):
                break
            self.out("  ! That doesn't look like an email address.")
        jurisdiction = self._ask("Country (and state, if relevant) whose law applies, e.g. 'Egypt' or 'California, USA':\n> ")
        effective = self._ask(f"Effective date [{self.today}]:\n> ", allow_empty=True) or self.today
        self.log.append("founder", "enter_business_details", approved_by="founder", jurisdiction=jurisdiction)
        return {"business_name": name, "contact_email": email, "jurisdiction": jurisdiction,
                "effective_date": effective}

    # ---------- step 3: policies ----------

    def policy_step(self, facts: dict) -> dict | None:
        self.out("\n=== Step 3: policy drafts ===")
        self._write_drafts(facts)
        reviewer = PolicyReviewer(self.llm)
        while True:
            texts = {k: (self.outputs / f).read_text() if (self.outputs / f).exists() else ""
                     for k, f in policies.FILES.items()}
            issues = policies.check_policies(texts, facts)
            self.out("(reviewer reading the drafts...)")
            review, res = reviewer.review(facts, texts)
            self._log_llm("policy_reviewer", "review_policies", res, issues=len(review["issues"]))
            self._write("phase4_step3_policy_review.md", render_review_md(issues, review))
            self.out(render_review_console(issues, review, self.outputs))

            fixes = [i for i in review["issues"] if i["severity"] == "fix"]
            options = {"r": "I edited the files: check them again",
                       "g": "Regenerate the drafts from the template (discards my edits)", "s": "Stop"}
            if not issues:
                label = f"Approve all three (I have read the {len(fixes)} points the reviewer asked to fix)" if fixes \
                    else "Approve all three"
                options = {"a": label, **options}
            pick = choose(self.input_fn, self.out, "\nReview the drafts:", options)
            if pick == "s":
                self.log.append("founder", "reject_policies", approved_by="founder", outcome="stop")
                return None
            if pick == "a":
                for name in policies.FILES:
                    self.log.append("founder", f"approve_{name}_policy", approved_by="founder",
                                    reviewer_fix_points_acknowledged=len([i for i in fixes if i["policy"] == name]))
                return texts
            if pick == "g":
                self._write_drafts(facts)

    def _write_drafts(self, facts):
        for name, text in policies.render_all(facts).items():
            self._write(policies.FILES[name], text)
        self.log.append("orchestrator", "draft_policies_from_template", files=list(policies.FILES.values()))


# ---------- rendering ----------

def render_pricing_console(p: dict, signups) -> str:
    be = pricing.break_even(p)
    lines = ["", f"  Price:   {pricing.price_line(p) if p['billing'] in pricing.BILLING else p['price']}",
             f"  Refunds: {p['refund_days']} days", f"  Running costs: {pricing.money(p['monthly_costs'], p['currency'])}"
             f"/month (ASSUMPTION)",
             f"  Break-even: {be['needed']} {be['unit']}" + (f" (waitlist: {signups})" if signups is not None else ""),
             "", f"  Why: {p['rationale']}"]
    if p["anchors"]:
        lines.append("  Compared with (ASSUMPTIONS): " + "; ".join(f"{a['alternative']} ({a['price']})" for a in p["anchors"]))
    if p["price_test"]:
        lines.append(f"  How to test this price: {p['price_test']}")
    return "\n".join(lines)


def render_pricing_md(p: dict, signups) -> str:
    be = pricing.break_even(p)
    lines = ["# Phase 4, Step 1: pricing", "", f"**Plan:** {pricing.price_line(p)}", "",
             f"**Billing:** {pricing.billing_note(p)}", "", f"**Refunds:** {pricing.refund_note(p)}", "",
             "Payments are not set up. Nothing is charged by this system.", "",
             "## Why this price", "", p["rationale"], "", f"**Value metric:** {p['value_metric']}", "",
             "## What the customer uses today", "", "| Alternative | Cost to them | Status |", "|---|---|---|"]
    lines += [f"| {a['alternative']} | {a['price']} | ASSUMPTION |" for a in p["anchors"]] or ["| (none given) | | |"]
    lines += ["", "## Running costs and break-even", "",
              f"Estimated running costs: {pricing.money(p['monthly_costs'], p['currency'])}/month (ASSUMPTION)."]
    lines += [f"- {b['item']}: {pricing.money(b['amount'], p['currency'])}" for b in p["monthly_costs_breakdown"]]
    lines += ["", f"Break-even: **{be['needed']} {be['unit']}**. Waitlist sign-ups from Phase 2: "
                  f"{signups if signups is not None else 'unknown'}.", "",
              "## Pricing page text", "", f"**{p['headline']}**", "", pricing.price_line(p), ""]
    lines += [f"- {x}" for x in p["includes"]]
    lines += ["", pricing.billing_note(p), "", pricing.refund_note(p), "",
              "## How to test the price before charging", "", p["price_test"] or "(none given)", "",
              "## Assumptions", ""] + ([f"- {a}" for a in p["assumptions"]] or ["- (none stated)"])
    return "\n".join(lines) + "\n"


def render_review_console(issues, review, outputs) -> str:
    lines = ["", "  Drafts (edit these files if you want changes):"]
    lines += [f"   - {outputs / f}" for f in policies.FILES.values()]
    lines.append("\n  Automatic checks: " + ("all pass" if not issues else f"{len(issues)} problem(s)"))
    lines += [f"   ! {i}" for i in issues]
    if review["issues"]:
        lines.append("\n  Reviewer notes (not legal advice):")
        lines += [f"   - [{i['severity']}] {i['policy']}: {i['issue']}" + (f" -> {i['suggestion']}" if i["suggestion"] else "")
                  for i in review["issues"]]
    if review["ask_a_lawyer"]:
        lines.append("  Ask a lawyer about: " + "; ".join(review["ask_a_lawyer"]))
    return "\n".join(lines)


def render_review_md(issues, review) -> str:
    lines = ["# Phase 4, Step 3: policy review", "",
             "Policies were filled in from fixed templates by code; the model did not write legal text. "
             "This review is not legal advice.", "", "## Automatic checks", ""]
    lines += [f"- FAIL: {i}" for i in issues] or ["- All checks pass."]
    lines += ["", "## Reviewer notes", "", review["summary"] or "", ""]
    lines += [f"- **[{i['severity']}] {i['policy']}:** {i['issue']}" + (f" Suggestion: {i['suggestion']}" if i["suggestion"] else "")
              for i in review["issues"]] or ["- No issues raised."]
    lines += ["", "## Topics to check with a lawyer", ""] + ([f"- {x}" for x in review["ask_a_lawyer"]] or ["- (none listed)"])
    return "\n".join(lines) + "\n"


def render_gate_console(gate) -> str:
    return "\n".join(["", "=== Commercial gate ===", "", f"  OUTCOME: {gate.outcome.upper()}"]
                     + [f"   - {r}" for r in gate.reasons])


def render_gate_md(gate, plan, decision) -> str:
    lines = ["# Phase 4 gate decision: commercial setup", "", f"**Outcome:** {gate.outcome.upper()}", "",
             f"**Founder decision:** {decision}", "", f"**Price:** {pricing.price_line(plan)}", "", "## Reasons", ""]
    lines += [f"- {r}" for r in gate.reasons]
    lines += ["", "## Rule", "", "REPEAT if the pricing or any policy draft fails an automatic check. PIVOT if covering "
              "running costs would need more paying customers than joined the waitlist. Otherwise GO, which the "
              "founder must approve. No payments are set up in this phase."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 4: Commercial setup")
    p.add_argument("--handoff")
    args = p.parse_args(argv)
    try:
        llm = LLMClient()
        print(f"Model: {llm.model} at {llm.base_url}")
        CommercialPipeline(llm).run(args.handoff)
    except (FileNotFoundError, LLMError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Nothing was published, sent or spent.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
