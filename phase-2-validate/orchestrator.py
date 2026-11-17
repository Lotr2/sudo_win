"""Phase 2: Validate. Design the business (Lean Canvas), then test demand with a waitlist landing page.

A demand test takes days, so the phase runs as three commands:

  plan   Step 1: Lean Canvas -> founder approves or edits.
         Step 2: demand-test plan with GO and STOP thresholds -> founder approves; the plan is then locked.
         Step 3: landing page -> founder approves -> published locally (the test clock starts).
  serve  Serve the approved page and collect waitlist sign-ups (local; public hosting is up to the founder).
  gate   Compare the evidence with the locked thresholds -> go / repeat / pivot / stop -> founder decides.

Usage:
  python phase-2-validate/orchestrator.py plan [--handoff outputs/phase1_step3_handoff.json]
  python phase-2-validate/orchestrator.py serve [--port 8000]
  python phase-2-validate/orchestrator.py gate [--signups N --visitors N --source "where the numbers came from"]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.copywriter import Copywriter, CopyRejected  # noqa: E402
from agents.experimenter import Experimenter, validate_plan  # noqa: E402
from agents.strategist import BOX_ORDER, LIST_BOXES, Strategist  # noqa: E402
from common.approval import FounderQuit, ask, choose  # noqa: E402
from common.decision_log import DecisionLog  # noqa: E402
from common.guardrails import REFUSAL, screen_request  # noqa: E402
from common.llm import LLMClient, LLMError  # noqa: E402
from gates import PlanTampered, demand_gate, plan_hash, verify_lock  # noqa: E402
from landing_page import publish, render, serve  # noqa: E402
from waitlist import Waitlist  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_PER_CALL = 0  # local open-weight model
PLAN_FILE = "phase2_step2_demand_test_plan.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ValidatePipeline:
    def __init__(self, llm=None, *, input_fn=input, output_fn=print, log_path=None, outputs_dir=None, runtime_dir=None):
        self.llm = llm
        self.input_fn = input_fn
        self.out = output_fn
        self.log = DecisionLog(log_path or REPO_ROOT / "decision_log.json")
        self.outputs = Path(outputs_dir or REPO_ROOT / "outputs")
        self.runtime = Path(runtime_dir or os.environ.get("RUNTIME_DIR") or REPO_ROOT / "runtime")
        self.site_dir = self.runtime / "site"
        self.waitlist = Waitlist(self.runtime / "waitlist")
        self.state_file = self.runtime / "phase2_state.json"

    # ---------- helpers ----------

    def _write(self, name: str, text: str) -> Path:
        self.outputs.mkdir(parents=True, exist_ok=True)
        (self.outputs / name).write_text(text)
        return self.outputs / name

    def _log_llm(self, agent, action, res, **extra):
        self.log.append(agent, action, model=res.model, cost=COST_PER_CALL,
                        tokens=res.prompt_tokens + res.completion_tokens, **extra)

    def _ask(self, prompt: str) -> str:
        while True:
            answer = ask(self.input_fn, prompt)
            hits = screen_request(answer)
            if not hits:
                return answer
            self.out(f"\n{REFUSAL}\n")
            self.log.append("guardrail", "refuse_request", outcome="refused", reasons=hits)

    def _save_state(self, **kw):
        state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        state.update(kw)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2) + "\n")

    def _state(self) -> dict:
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    # ---------- plan ----------

    def plan(self, handoff_path: Path | None = None) -> dict:
        try:
            return self._plan(Path(handoff_path or self.outputs / "phase1_step3_handoff.json"))
        except FounderQuit:
            self.log.append("founder", "quit", approved_by="founder", outcome="stop")
            self.out("\nStopped by founder. Nothing was published, sent or spent.")
            return {"outcome": "stop"}

    def _plan(self, handoff_path: Path) -> dict:
        if not handoff_path.exists():
            raise FileNotFoundError(f"{handoff_path} not found. Phase 1 must end with a founder-approved GO first.")
        handoff = json.loads(handoff_path.read_text())
        self.log.append("orchestrator", "load_phase1_handoff", source_file=str(handoff_path.name))

        canvas = self.canvas_step(handoff)
        if canvas is None:
            return {"outcome": "stop", "step": "canvas"}
        locked = self.plan_step(handoff["brief"], canvas)
        if locked is None:
            return {"outcome": "stop", "step": "plan"}
        page = self.page_step(canvas)
        if page is None:
            return {"outcome": "stop", "step": "landing_page"}

        self.out("\nThe demand test has started. Next:\n"
                 "  1. python phase-2-validate/orchestrator.py serve   (open http://127.0.0.1:8000)\n"
                 "  2. Share the page yourself, as yourself, in the channels from the plan.\n"
                 "     This system does not post anything for you.\n"
                 f"  3. After {locked['plan']['duration_days']} days (or once the target is hit): "
                 "python phase-2-validate/orchestrator.py gate")
        return {"outcome": "started", "plan": locked}

    def canvas_step(self, handoff: dict) -> dict | None:
        self.out("\n=== Step 1: Lean Canvas ===\n(thinking...)")
        strategist = Strategist(self.llm)
        canvas, res = strategist.build(handoff)
        self._log_llm("strategist", "build_lean_canvas", res)
        while True:
            self.out(render_canvas_console(canvas))
            pick = choose(self.input_fn, self.out, "\nReview the canvas:",
                          {"a": "Approve", "e": "Edit one box myself", "r": "Regenerate with my feedback",
                           "s": "Stop"})
            if pick == "s":
                self.log.append("founder", "reject_canvas", approved_by="founder", outcome="stop")
                return None
            if pick == "a":
                self.log.append("founder", "approve_canvas", approved_by="founder")
                self._write("phase2_step1_lean_canvas.md", render_canvas_md(canvas, handoff["concept"]))
                self._save_state(brief=handoff["brief"], concept=handoff["concept"], canvas=canvas)
                return canvas
            if pick == "e":
                self.edit_box(canvas)
                continue
            feedback = self._ask("What should change?\n> ")
            self.out("(thinking...)")
            canvas, res = strategist.build(handoff, feedback=feedback, previous=canvas)
            self._log_llm("strategist", "revise_lean_canvas", res)

    def edit_box(self, canvas: dict):
        for i, box in enumerate(BOX_ORDER, 1):
            self.out(f"  [{i}] {box.replace('_', ' ')}")
        while True:
            n = ask(self.input_fn, "Box number: ")
            if n.isdigit() and 1 <= int(n) <= len(BOX_ORDER):
                break
        box = BOX_ORDER[int(n) - 1]
        hint = " (separate items with ;)" if box in LIST_BOXES else ""
        text = self._ask(f"New text for {box.replace('_', ' ')}{hint}:\n> ")
        canvas[box] = [t.strip() for t in text.split(";") if t.strip()] if box in LIST_BOXES else text
        self.log.append("founder", "edit_canvas", approved_by="founder", box=box)

    def plan_step(self, brief: dict, canvas: dict) -> dict | None:
        self.out("\n=== Step 2: demand-test plan ===\n(thinking...)")
        experimenter = Experimenter(self.llm)
        plan, res = experimenter.design(brief, canvas)
        self._log_llm("experimenter", "design_demand_test", res)
        while True:
            self.out(render_plan_console(plan))
            problems = validate_plan(plan)
            for p in problems:
                self.out(f"  ! {p}")
            options = {"e": "Edit the numbers", "r": "Regenerate with my feedback", "s": "Stop"}
            if not problems:
                options = {"a": "Approve and lock these thresholds", **options}
            pick = choose(self.input_fn, self.out,
                          "\nOnce approved, these thresholds are locked and cannot change after the test starts.",
                          options)
            if pick == "s":
                self.log.append("founder", "reject_demand_test_plan", approved_by="founder", outcome="stop")
                return None
            if pick == "a":
                locked = {"plan": plan, "locked_at": _now(), "sha256": plan_hash(plan), "started_at": None}
                self._write(PLAN_FILE, json.dumps(locked, indent=2) + "\n")
                self.log.append("founder", "approve_demand_test_plan", approved_by="founder",
                                plan_sha256=locked["sha256"], target_signups=plan["target_signups"],
                                stop_below_signups=plan["stop_below_signups"])
                return locked
            if pick == "e":
                for key in ("duration_days", "min_visitors", "target_signups", "stop_below_signups"):
                    v = ask(self.input_fn, f"{key.replace('_', ' ')} [{plan[key]}]: ", allow_empty=True)
                    if v.isdigit():
                        plan[key] = int(v)
                self.log.append("founder", "edit_demand_test_plan", approved_by="founder")
                continue
            feedback = self._ask("What should change?\n> ")
            self.out("(thinking...)")
            plan, res = experimenter.design(brief, canvas, feedback=feedback)
            self._log_llm("experimenter", "revise_demand_test", res)

    def page_step(self, canvas: dict) -> str | None:
        self.out("\n=== Step 3: landing page ===")
        writer = Copywriter(self.llm)
        feedback = None
        while True:
            self.out("(thinking...)")
            try:
                copy, results, rejected = writer.write(canvas, feedback)
            except CopyRejected as e:
                self.log.append("guardrail", "reject_landing_copy", outcome="rejected", reasons=e.issues)
                self.out(f"\nEvery draft broke the copy rules ({', '.join(e.issues)}), so none is shown.")
                pick = choose(self.input_fn, self.out, "What next?", {"r": "Try again", "s": "Stop"})
                if pick == "s":
                    return None
                continue
            for res in results:
                self._log_llm("copywriter", "write_landing_copy", res)
            for issues in rejected:
                self.log.append("guardrail", "reject_landing_copy", outcome="regenerated", reasons=issues)
            page = render(copy)
            preview = self._write("phase2_step3_landing_page.html", page)
            self.out(f"\nHeadline:    {copy['headline']}\nSubheadline: {copy['subheadline']}\n"
                     f"Button:      {copy['cta_label']}\n\nFull preview: {preview}")
            pick = choose(self.input_fn, self.out, "\nPublish this page? (local only; nothing is posted anywhere)",
                          {"p": "Approve and publish", "r": "Rewrite with my feedback", "s": "Stop"})
            if pick == "s":
                self.log.append("founder", "reject_landing_page", approved_by="founder", outcome="stop")
                return None
            if pick == "r":
                feedback = self._ask("What should change?\n> ")
                continue
            record = publish(self.site_dir, page)
            self.log.append("founder", "publish_landing_page", approved_by="founder", page_sha256=record["sha256"])
            plan_path = self.outputs / PLAN_FILE
            locked = json.loads(plan_path.read_text())
            locked["started_at"] = record["approved_at"]
            plan_path.write_text(json.dumps(locked, indent=2) + "\n")
            self._save_state(copy=copy)
            return page

    # ---------- gate ----------

    def gate(self, evidence: dict | None = None, now: datetime | None = None) -> dict:
        try:
            return self._gate(evidence, now)
        except FounderQuit:
            self.out("\nStopped. Nothing was decided.")
            return {"outcome": "quit"}

    def _approved_plan_hash(self) -> str | None:
        approvals = [e for e in self.log.entries() if e.get("action") == "approve_demand_test_plan"]
        return approvals[-1].get("plan_sha256") if approvals else None

    def _gate(self, evidence, now):
        plan_path = self.outputs / PLAN_FILE
        if not plan_path.exists():
            raise FileNotFoundError("No demand-test plan. Run the `plan` step first.")
        locked = json.loads(plan_path.read_text())
        verify_lock(locked, self._approved_plan_hash())
        if not locked.get("started_at"):
            raise RuntimeError("The landing page was never published, so the test has not started.")

        if evidence is None:
            evidence = {**self.waitlist.counts(), "source": "local waitlist server", "verified": True}
        result = demand_gate({**locked, "locked_at": locked["started_at"]}, evidence, now)
        self.out(render_gate_console(locked["plan"], evidence, result))
        self.log.append("gate", "demand_gate", outcome=result.outcome, reasons=result.reasons,
                        visitors=evidence["visitors"], signups=evidence["signups"],
                        evidence_source=evidence.get("source"), evidence_verified=evidence.get("verified", False))

        options = {
            "go": {"a": "Approve: move to Phase 3 (Build)", "s": "Stop"},
            "repeat": {"k": "Keep the test running and check again later", "s": "Stop"},
            "pivot": {"t": "Change the customer, message or offer and run a new test",
                      "d": "Go back to Phase 1 (Discover)", "s": "Stop"},
            "stop": {"d": "Go back to Phase 1 (Discover) with a different idea", "s": "Stop"},
        }[result.outcome]
        pick = choose(self.input_fn, self.out, f"\nOutcome: {result.outcome.upper()}. What next?", options)
        decision = {"a": "approve", "s": "stop", "k": "keep_running", "t": "new_test", "d": "back_to_discover"}[pick]
        self.log.append("founder", "demand_gate_decision", approved_by="founder", outcome=result.outcome,
                        founder_decision=decision)
        self._write("phase2_gate_demand_test.md", render_gate_md(locked, evidence, result, decision))

        if decision == "approve":
            state = self._state()
            self._write("phase2_step4_handoff.json", json.dumps({
                "brief": state.get("brief"), "concept": state.get("concept"), "canvas": state.get("canvas"),
                "landing_copy": state.get("copy"), "demand_test": locked, "evidence": evidence,
            }, indent=2) + "\n")
            self.out("\nApproved. Handed to Phase 3 (Build).")
        elif decision == "new_test":
            self.out("\nRun `plan` again to write a new canvas, test and page. The old result stays in the log.")
        elif decision == "back_to_discover":
            self.out("\nRun Phase 1 again: python phase-1-discover/orchestrator.py")
        return {"outcome": result.outcome, "decision": decision}


# ---------- rendering ----------

def _box(v) -> str:
    return "; ".join(v) if isinstance(v, list) else str(v)


def render_canvas_console(c: dict) -> str:
    lines = [""]
    for box in BOX_ORDER:
        lines.append(f"  {box.replace('_', ' ').upper()}: {_box(c[box])}")
    if c["riskiest_assumptions"]:
        lines.append("\n  RISKIEST ASSUMPTIONS:")
        lines += [f"   - {r['assumption']} (test: {r['how_to_test']})" for r in c["riskiest_assumptions"]]
    return "\n".join(lines)


def render_canvas_md(c: dict, concept: dict) -> str:
    lines = ["# Phase 2, Step 1: Lean Canvas", "", f"**Concept:** {concept.get('one_liner', '')}", "",
             "| Box | Content |", "|---|---|"]
    lines += [f"| {b.replace('_', ' ').title()} | {_box(c[b])} |" for b in BOX_ORDER]
    if c["riskiest_assumptions"]:
        lines += ["", "## Riskiest assumptions", "", "| Assumption | Why risky | How to test |", "|---|---|---|"]
        lines += [f"| {r['assumption']} | {r['why_risky']} | {r['how_to_test']} |" for r in c["riskiest_assumptions"]]
    lines += ["", "## Market assumptions", "", "No web research was done; these are untested."]
    lines += [f"- {a}" for a in c["assumptions"]] or ["- (none stated)"]
    return "\n".join(lines) + "\n"


def render_plan_console(p: dict) -> str:
    lines = ["", f"  Hypothesis: {p['hypothesis']}", "",
             f"  Duration:          {p['duration_days']} days",
             f"  Minimum visitors:  {p['min_visitors']}  (fewer = no conclusion)",
             f"  GO at:             {p['target_signups']}+ sign-ups",
             f"  STOP below:        {p['stop_below_signups']} sign-ups",
             f"  PIVOT:             in between"]
    if p["assumed_conversion_rate"] is not None:
        lines.append(f"  Assumed conversion: {p['assumed_conversion_rate']:.0%} (ASSUMPTION)")
    if p["channels"]:
        lines.append("\n  Where you would share it (you post, as yourself):")
        lines += [f"   - {c['where']}: {c['how']} (~{c['expected_visitors']} visitors, ASSUMPTION)" for c in p["channels"]]
    if p["rationale"]:
        lines.append(f"\n  Why these numbers: {p['rationale']}")
    return "\n".join(lines)


def render_gate_console(plan, ev, result) -> str:
    return "\n".join(["", "=== Demand gate ===", "",
                      f"  Visitors: {ev['visitors']} (need {plan['min_visitors']})",
                      f"  Sign-ups: {ev['signups']} (GO at {plan['target_signups']}, STOP below {plan['stop_below_signups']})",
                      f"  Source:   {ev.get('source')}{'' if ev.get('verified') else ' (founder-reported)'}",
                      "", f"  OUTCOME: {result.outcome.upper()}"] + [f"   - {r}" for r in result.reasons])


def render_gate_md(locked, ev, result, decision) -> str:
    p = locked["plan"]
    lines = ["# Phase 2 gate decision: demand test", "", f"**Outcome:** {result.outcome.upper()}", "",
             f"**Founder decision:** {decision}", "", "## Evidence", "",
             f"- Visitors: {ev['visitors']}", f"- Waitlist sign-ups: {ev['signups']}",
             f"- Source: {ev.get('source')}{'' if ev.get('verified') else ' (founder-reported, not verified by the system)'}",
             "", "## Thresholds (locked before the test)", "",
             f"- Hypothesis: {p['hypothesis']}",
             f"- Duration: {p['duration_days']} days, from {locked.get('started_at')}",
             f"- GO at {p['target_signups']}+ sign-ups; STOP below {p['stop_below_signups']}; PIVOT in between",
             f"- No conclusion below {p['min_visitors']} visitors (REPEAT)",
             f"- Plan SHA-256: `{locked['sha256']}`", "", "## Reasons", ""]
    lines += [f"- {r}" for r in result.reasons]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 2: Validate")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan")
    sp.add_argument("--handoff")
    ss = sub.add_parser("serve")
    ss.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ss.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    sg = sub.add_parser("gate")
    sg.add_argument("--signups", type=int, help="founder-reported sign-ups from your own hosting")
    sg.add_argument("--visitors", type=int)
    sg.add_argument("--source", help="where the numbers came from")
    args = p.parse_args(argv)

    try:
        if args.cmd == "plan":
            llm = LLMClient()
            print(f"Model: {llm.model} at {llm.base_url}")
            ValidatePipeline(llm).plan(args.handoff)
        elif args.cmd == "serve":
            pipe = ValidatePipeline()
            server = serve(pipe.site_dir, pipe.waitlist, args.host, args.port)
            print(f"Serving the approved landing page at http://{args.host}:{args.port}  (Ctrl+C to stop)")
            server.serve_forever()
        else:
            evidence = None
            if args.signups is not None:
                if args.visitors is None or not args.source:
                    p.error("--signups needs --visitors and --source")
                evidence = {"signups": args.signups, "visitors": args.visitors, "source": args.source, "verified": False}
            ValidatePipeline().gate(evidence)
    except (FileNotFoundError, PermissionError, PlanTampered, RuntimeError, LLMError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Nothing was published, sent or spent.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
