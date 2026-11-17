"""Phase 3: Build. Turn the validated business into a tested MVP with user docs.

  Step 1  Product manager writes a testable MVP spec -> founder approves, cuts stories, or asks for changes.
  Step 2  Tester writes the tests from the spec (before any code). Coder writes logic.js and fixes it until the
          tests pass, in a sandbox. The coder cannot change the tests.
  Step 3  UI builder writes index.html on top of the tested logic.
  Step 4  Doc writer writes the user guide.
  Gate    Every criterion has a passing test + all checks clean -> founder tries the app -> approves.

Usage:
  python phase-3-build/orchestrator.py build [--handoff outputs/phase2_step4_handoff.json]
  python phase-3-build/orchestrator.py serve [--port 8001]
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sandbox  # noqa: E402
from agents.engineers import Coder, DocWriter, GenerationFailed, Tester, UIBuilder  # noqa: E402
from agents.product_manager import ProductManager, api_names, criteria, drop_stories, validate_spec  # noqa: E402
from common.approval import FounderQuit, ask, choose  # noqa: E402
from common.decision_log import DecisionLog  # noqa: E402
from common.guardrails import REFUSAL, screen_request  # noqa: E402
from common.llm import LLMClient, LLMError  # noqa: E402
from gates import ac_status, build_gate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_PER_CALL = 0  # local open-weight model


class BuildPipeline:
    def __init__(self, llm=None, *, input_fn=input, output_fn=print, log_path=None, outputs_dir=None,
                 runtime_dir=None, fix_attempts: int = 5):
        self.llm = llm
        self.input_fn = input_fn
        self.out = output_fn
        self.log = DecisionLog(log_path or REPO_ROOT / "decision_log.json")
        self.outputs = Path(outputs_dir or REPO_ROOT / "outputs")
        self.runtime = Path(runtime_dir or os.environ.get("RUNTIME_DIR") or REPO_ROOT / "runtime")
        self.product_dir = self.runtime / "product"
        self.fix_attempts = fix_attempts

    # ---------- helpers ----------

    def _write(self, name: str, text: str) -> Path:
        self.outputs.mkdir(parents=True, exist_ok=True)
        (self.outputs / name).write_text(text)
        return self.outputs / name

    def _file(self, name: str, text: str):
        self.product_dir.mkdir(parents=True, exist_ok=True)
        (self.product_dir / name).write_text(text)

    def _log_llm(self, agent, action, results, **extra):
        for res in results if isinstance(results, list) else [results]:
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

    # ---------- main ----------

    def build(self, handoff_path: Path | None = None) -> dict:
        try:
            return self._build(Path(handoff_path or self.outputs / "phase2_step4_handoff.json"))
        except FounderQuit:
            self.log.append("founder", "quit", approved_by="founder", outcome="stop")
            self.out("\nStopped by founder. Nothing was published, sent or spent.")
            return {"outcome": "stop"}

    def _build(self, handoff_path: Path) -> dict:
        if not handoff_path.exists():
            raise FileNotFoundError(f"{handoff_path} not found. Phase 2 must end with a founder-approved GO first.")
        handoff = json.loads(handoff_path.read_text())
        self.handoff = handoff
        self.log.append("orchestrator", "load_phase2_handoff", source_file=handoff_path.name)

        spec = self.spec_step(handoff)
        if spec is None:
            return {"outcome": "stop", "step": "spec"}
        if not spec["buildable"]:
            return self.finish_not_buildable(spec)

        built = self.code_step(spec)
        if built is None:
            return {"outcome": "stop", "step": "code"}
        spec, tests, logic, run = built
        ui = self.ui_step(spec, logic)
        if ui is None:
            return {"outcome": "stop", "step": "ui"}
        docs = self.docs_step(spec, run)
        return self.gate_step(spec, tests, logic, ui, docs, run)

    # ---------- step 1: spec ----------

    def spec_step(self, handoff: dict) -> dict | None:
        self.out("\n=== Step 1: MVP spec ===\n(thinking...)")
        pm = ProductManager(self.llm)
        spec, res = pm.write_spec(handoff)
        self._log_llm("product_manager", "write_mvp_spec", res)
        while True:
            problems = validate_spec(spec)
            self.out(render_spec_console(spec))
            for p in problems:
                self.out(f"  ! {p}")
            if not spec["buildable"]:
                pick = choose(self.input_fn, self.out, "\nThe product manager says this cannot be built here.",
                              {"r": "Try again with my feedback", "s": "Accept and stop"})
                if pick == "s":
                    return spec
            else:
                options = {"d": "Drop a story (smaller MVP)", "r": "Change it with my feedback", "s": "Stop"}
                if not problems:
                    options = {"a": "Approve this spec", **options}
                pick = choose(self.input_fn, self.out, "\nReview the MVP spec:", options)
                if pick == "s":
                    self.log.append("founder", "reject_mvp_spec", approved_by="founder", outcome="stop")
                    return None
                if pick == "a":
                    self.log.append("founder", "approve_mvp_spec", approved_by="founder",
                                    criteria=len(criteria(spec)))
                    self._write("phase3_step1_mvp_spec.md", render_spec_md(spec))
                    return spec
                if pick == "d":
                    sid = ask(self.input_fn, "Story id to drop (e.g. S2): ").upper()
                    if any(s["id"].upper() == sid for s in spec["stories"]):
                        spec = drop_stories(spec, {s["id"] for s in spec["stories"] if s["id"].upper() == sid})
                        self.log.append("founder", "cut_story", approved_by="founder", story=sid)
                    continue
            feedback = self._ask("What should change?\n> ")
            self.out("(thinking...)")
            spec, res = pm.write_spec(handoff, feedback=feedback, previous=spec)
            self._log_llm("product_manager", "revise_mvp_spec", res)

    def finish_not_buildable(self, spec):
        reason = spec.get("not_buildable_reason") or "not stated"
        gate = build_gate(spec, sandbox.TestRun(), {}, False)
        self.log.append("gate", "build_gate", outcome="stop", reasons=gate.reasons)
        pick = choose(self.input_fn, self.out, f"\nSTOP: {reason}\nWhat next?",
                      {"b": "Go back to Phase 2 (Validate) and reshape the offer", "s": "Stop"})
        decision = "back_to_validate" if pick == "b" else "stop"
        self.log.append("founder", "build_gate_decision", approved_by="founder", outcome="stop",
                        founder_decision=decision)
        self._write("phase3_gate_tests.md", render_gate_md(spec, gate, sandbox.TestRun(), decision))
        return {"outcome": "stop", "decision": decision}

    # ---------- step 2: tests first, then code ----------

    def code_step(self, spec: dict):
        """Returns (spec, tests, logic, run) once all tests pass, or None if the founder stops."""
        while True:
            self.out("\n=== Step 2: tests first, then code ===")
            if self.product_dir.exists():
                shutil.rmtree(self.product_dir)
            try:
                self.out("(tester writing tests from the spec...)")
                tests, results = Tester(self.llm).write_tests(spec)
                self._log_llm("tester", "write_tests", results, criteria=len(criteria(spec)))
                self._file("logic.test.js", tests)
                logic, run = self.fix_loop(spec, tests, None, self.fix_attempts)
            except GenerationFailed as e:
                self.log.append("guardrail", "reject_generated_code", outcome="rejected", reasons=e.issues)
                self.out(f"\nGenerated code kept failing checks: {'; '.join(e.issues)}")
                pick = choose(self.input_fn, self.out, "What next?", {"t": "Try again", "s": "Stop"})
                if pick == "s":
                    return None
                continue

            while not run.ok:
                status = ac_status(spec, run)
                failing_stories = sorted({s["id"] for s in spec["stories"] for a in s["acceptance_criteria"]
                                          if status.get(a["id"]) != "pass"})
                self.out(f"\nAfter {self.fix_attempts} fix attempts: {len(run.passed)} passed, "
                         f"{len(run.failed)} failed. Stories not working yet: {', '.join(failing_stories) or '-'}")
                if run.error:
                    self.out(f"  Error: {run.error[:300]}")
                options = {"f": f"Keep fixing ({self.fix_attempts} more attempts)", "s": "Stop"}
                if failing_stories and len(failing_stories) < len(spec["stories"]):
                    options = {"f": options["f"], "c": f"Cut scope: drop {', '.join(failing_stories)}", "s": "Stop"}
                pick = choose(self.input_fn, self.out, "What next?", options)
                if pick == "s":
                    self.log.append("founder", "stop_build", approved_by="founder", outcome="stop")
                    return None
                if pick == "c":
                    spec = drop_stories(spec, set(failing_stories))
                    self.log.append("founder", "cut_story", approved_by="founder", story=",".join(failing_stories))
                    self._write("phase3_step1_mvp_spec.md", render_spec_md(spec))
                    break  # regenerate tests and code for the smaller spec
                try:
                    logic, run = self.fix_loop(spec, tests, logic, self.fix_attempts, run)
                except GenerationFailed as e:
                    self.log.append("guardrail", "reject_generated_code", outcome="rejected", reasons=e.issues)
                    self.out(f"\nGenerated code kept failing checks: {'; '.join(e.issues)}")
            else:
                return spec, tests, logic, run

    def fix_loop(self, spec, tests, logic, attempts, run=None):
        coder = Coder(self.llm)
        for i in range(attempts):
            failures = None if run is None else (run.failures or run.error)
            self.out(f"(coder {'writing' if logic is None else 'fixing'} logic.js, attempt {i + 1})")
            logic, results = coder.write_code(spec, tests, previous=logic, failures=failures)
            self._log_llm("coder", "write_logic" if failures is None else "fix_logic", results)
            self._file("logic.js", logic)
            run = sandbox.run_tests(self.product_dir)
            self.log.append("sandbox", "run_tests", passed=len(run.passed), failed=len(run.failed),
                            error=run.error or None)
            self.out(f"  tests: {len(run.passed)} passed, {len(run.failed)} failed")
            if run.ok:
                break
        return logic, run

    # ---------- steps 3 and 4 ----------

    def ui_step(self, spec, logic, feedback=None):
        self.out("\n=== Step 3: interface ===\n(thinking...)")
        while True:
            try:
                html, results = UIBuilder(self.llm).write_ui(spec, logic, feedback)
                self._log_llm("ui_builder", "write_ui", results)
                self._file("index.html", html)
                return html
            except GenerationFailed as e:
                self.log.append("guardrail", "reject_generated_code", outcome="rejected", reasons=e.issues)
                self.out(f"\nThe interface kept failing checks: {'; '.join(e.issues)}")
                if choose(self.input_fn, self.out, "What next?", {"t": "Try again", "s": "Stop"}) == "s":
                    return None

    def docs_step(self, spec, run):
        self.out("\n=== Step 4: user guide ===\n(thinking...)")
        try:
            docs, results = DocWriter(self.llm).write_docs(spec, f"{len(run.passed)} tests passed, 0 failed")
            self._log_llm("doc_writer", "write_user_guide", results)
            self._file("README.md", docs)
            self._write("phase3_step4_user_docs.md", docs)
            return docs
        except GenerationFailed as e:
            self.log.append("guardrail", "reject_generated_docs", outcome="rejected", reasons=e.issues)
            return None

    # ---------- gate ----------

    def gate_step(self, spec, tests, logic, ui, docs, run):
        names = api_names(spec)
        scans = {"logic.js": sandbox.scan_logic(logic, names),
                 "logic.test.js": sandbox.scan_tests(tests, [a["id"] for a in criteria(spec)]),
                 "index.html": sandbox.scan_ui(ui, names)}
        gate = build_gate(spec, run, scans, docs is not None)
        self._write("phase3_step2_test_report.md", render_test_report(spec, run))
        self.out(render_gate_console(gate))
        self.log.append("gate", "build_gate", outcome=gate.outcome, reasons=gate.reasons,
                        tests_passed=len(run.passed), tests_failed=len(run.failed))

        if gate.outcome == "go":
            self.out(f"\nTry it before approving:  python phase-3-build/orchestrator.py serve\n"
                     f"  (product files: {self.product_dir})")
            options = {"a": "Approve the MVP (I tried it)", "u": "Change the interface with my feedback", "s": "Stop"}
        else:
            options = {"u": "Rebuild the interface and user guide", "s": "Stop"}
        pick = choose(self.input_fn, self.out, f"\nOutcome: {gate.outcome.upper()}. What next?", options)
        decision = {"a": "approve", "u": "rebuild_ui", "s": "stop"}[pick]
        self.log.append("founder", "build_gate_decision", approved_by="founder", outcome=gate.outcome,
                        founder_decision=decision)
        self._write("phase3_gate_tests.md", render_gate_md(spec, gate, run, decision))

        if decision == "rebuild_ui":
            feedback = self._ask("What should change in the interface?\n> ") if gate.outcome == "go" else None
            new_ui = self.ui_step(spec, logic, feedback)
            if new_ui is None:
                return {"outcome": "stop", "step": "ui"}
            docs = docs if docs is not None else self.docs_step(spec, run)
            return self.gate_step(spec, tests, logic, new_ui, docs, run)
        if decision == "approve":
            prior = getattr(self, "handoff", {})
            self._write("phase3_step5_handoff.json", json.dumps({
                **{k: prior.get(k) for k in ("brief", "concept", "canvas", "evidence")},
                "spec": spec, "product_dir": str(self.product_dir),
                "tests": {"passed": run.passed, "failed": run.failed}, "user_guide": docs,
            }, indent=2) + "\n")
            self.out("\nApproved. MVP handed to Phase 4 (Commercial setup).")
            return {"outcome": "go", "spec": spec}
        self.out("\nStopped. Nothing was published, sent or spent.")
        return {"outcome": "stop", "gate": gate.outcome}


# ---------- rendering ----------

def render_spec_console(spec: dict) -> str:
    if not spec["buildable"]:
        return f"\n  NOT BUILDABLE: {spec.get('not_buildable_reason')}"
    lines = ["", f"  {spec['product_name']}: {spec['summary']}", ""]
    for s in spec["stories"]:
        lines.append(f"  [{s['id']}] {s['story']}")
        lines += [f"      {a['id']}: {a['text']}" for a in s["acceptance_criteria"]]
    lines.append(f"\n  Functions: {', '.join(api_names(spec))}")
    if spec["out_of_scope"]:
        lines.append("  Out of scope: " + "; ".join(spec["out_of_scope"]))
    return "\n".join(lines)


def render_spec_md(spec: dict) -> str:
    lines = ["# Phase 3, Step 1: MVP spec", "", f"**{spec['product_name']}**: {spec['summary']}", "",
             "Form: single-page web app that runs in the browser; data stays on the device.", "",
             "## User stories and acceptance criteria", ""]
    for s in spec["stories"]:
        lines += [f"### {s['id']}: {s['story']}", ""]
        lines += [f"- **{a['id']}** (`{a['function']}`): {a['text']}" for a in s["acceptance_criteria"]]
        lines.append("")
    lines += ["## Logic API", "", "| Function | Signature | Description |", "|---|---|---|"]
    lines += [f"| `{f['name']}` | `{f['signature']}` | {f['description']} |" for f in spec["api"]]
    lines += ["", f"**Data model:** {spec['data_model']}", "", "## Out of scope", ""]
    lines += [f"- {x}" for x in spec["out_of_scope"]] or ["- (none)"]
    return "\n".join(lines) + "\n"


def render_test_report(spec, run) -> str:
    status = ac_status(spec, run)
    lines = ["# Phase 3, Step 2: test report", "",
             "Tests were written by the Tester agent from the spec before any code existed. The Coder could not "
             "change them. They ran in Node's permission sandbox (no network, no writes, reads limited to the build folder).",
             "", f"**Result:** {len(run.passed)} passed, {len(run.failed)} failed, {run.skipped} skipped, {run.todo} todo",
             "", "## Acceptance criteria", "", "| Criterion | Status | Text |", "|---|---|---|"]
    lines += [f"| {a['id']} | {status[a['id']].upper()} | {a['text']} |" for a in criteria(spec)]
    lines += ["", "## Tests", ""] + [f"- PASS: {t}" for t in run.passed] + [f"- FAIL: {t}" for t in run.failed]
    if run.error:
        lines += ["", "## Error", "", "```", run.error, "```"]
    return "\n".join(lines) + "\n"


def render_gate_console(gate) -> str:
    lines = ["", "=== Build gate ===", ""]
    lines += [f"  [{'x' if c.passed else ' '}] {c.name}" + (f"  ({c.detail})" if c.detail else "") for c in gate.checks]
    return "\n".join(lines + ["", f"  OUTCOME: {gate.outcome.upper()}"] + [f"   - {r}" for r in gate.reasons])


def render_gate_md(spec, gate, run, decision) -> str:
    lines = ["# Phase 3 gate decision: tests", "", f"**Outcome:** {gate.outcome.upper()}", "",
             f"**Founder decision:** {decision}", "", "## Checks", ""]
    lines += [f"- [{'x' if c.passed else ' '}] {c.name}" + (f": {c.detail}" if c.detail else "") for c in gate.checks]
    lines += ["", "## Reasons", ""] + [f"- {r}" for r in gate.reasons]
    lines += ["", "## Rule", "", "GO needs every acceptance criterion to have a passing test written before the code, "
              "no failing/skipped/todo tests, clean safety scans, and a user guide. The founder must then try the "
              "product and approve it."]
    return "\n".join(lines) + "\n"


def make_server(product_dir: Path, host: str, port: int) -> ThreadingHTTPServer:
    if not (product_dir / "index.html").exists():
        raise FileNotFoundError(f"No product at {product_dir}. Run the `build` step first.")
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), functools.partial(Quiet, directory=str(product_dir)))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 3: Build")
    sub = p.add_subparsers(dest="cmd", required=True)
    sb = sub.add_parser("build")
    sb.add_argument("--handoff")
    sb.add_argument("--fix-attempts", type=int, default=int(os.environ.get("BUILD_FIX_ATTEMPTS", 5)))
    ss = sub.add_parser("serve")
    ss.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ss.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8001)))
    args = p.parse_args(argv)
    try:
        if args.cmd == "build":
            sandbox.node_path()
            llm = LLMClient()
            print(f"Model: {llm.model} at {llm.base_url}")
            BuildPipeline(llm, fix_attempts=args.fix_attempts).build(args.handoff)
        else:
            server = make_server(BuildPipeline().product_dir, args.host, args.port)
            print(f"Serving the MVP at http://{args.host}:{args.port}  (Ctrl+C to stop)")
            server.serve_forever()
    except (FileNotFoundError, RuntimeError, LLMError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Nothing was published, sent or spent.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
