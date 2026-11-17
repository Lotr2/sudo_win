"""Evals: run each brief in evals/cases.json up to its first stage decision and check the outcome and the
quality of what the founder would read. Use it whenever a prompt or a skill changes.

    python evals/run_evals.py                 # real models and live search (demo mode, a few minutes per case)
    python evals/run_evals.py --only fake_reviews,crypto_signals
    python evals/run_evals.py --fake          # the test fakes: checks the harness itself (seconds)
    python evals/run_evals.py --save          # also write evals/results.md (only from a real run)

Nothing is approved or decided on the founder's behalf: questions time out (as in demo mode), and the run stops at
the first stage check, before any founder decision.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# What the founder reads must never contain internal codes, raw data or labels meant for code.
UNREADABLE = [
    (r"```", "a raw code or JSON block"),
    (r"\b(as|ev|log|doc|ob)_\d+\b", "an internal id"),
    (r"\bv\d\b(?!\.)", "a version id like v1 instead of its name"),
    (r"\b(budget_usd|hours_per_week|pain_signals|pain_domains|fit_rules_pass|founder_reported|template_pack|web_tool)\b",
     "a field name"),
    (r"matched rule R\d+", "a rule code"),
    (r"\b0\.\d{2,}\b", "a raw decimal score"),
]


def readability_problems(text: str, where: str) -> list[str]:
    out = []
    for rx, what in UNREADABLE:
        m = re.search(rx, text)
        if m:
            out.append(f"{where}: {what} ({m.group(0)!r})")
    return out


def run_case(case: dict, settings, factory) -> dict:
    brief = (ROOT / case["brief"]).read_text() if case["brief"].endswith(".md") else case["brief"]
    started = time.time()
    rt = factory(settings, brief)
    p = rt.advance()
    while p and p["kind"] in {"question", "readback", "notes"}:
        p = rt.timeout()
    outcome = p.get("recommended") if p and p["kind"] == "gate" else None
    problems = []
    if outcome not in case["expect"]["gate1_in"]:
        problems.append(f"Stage 1 recommended {outcome!r}; expected one of {case['expect']['gate1_in']} "
                        f"({case['expect']['why']})")
    if p and p["kind"] == "gate":
        for r in p["reasons"]:
            problems += readability_problems(r, "stage reason")
        report = (rt.ctx.store.outputs / p["memo_file"]).read_text()
        problems += readability_problems(report, p["memo_file"])
        if p.get("versions") and "How each version scored" not in report:
            problems.append("the report does not explain how each version scored")
        if len(p.get("directions") or []) < 2:
            problems.append("fewer than 2 other directions were suggested")
        walk = [t["text"] for t in rt.ctx.store.conversation() if t.get("kind") == "gate"]
        if walk:
            problems += readability_problems(walk[-1], "advisor's explanation")
    for doc in sorted(rt.ctx.store.outputs.glob("*.md")):
        problems += readability_problems(doc.read_text(), doc.name)
    log = rt.ctx.store.log.entries
    return {"id": case["id"], "outcome": outcome, "expected": case["expect"]["gate1_in"], "problems": problems,
            "seconds": round(time.time() - started), "tokens": sum(int(e.get("tokens") or 0) for e in log),
            "run_id": rt.run_id}


def run_classifier_cases(settings) -> list[dict]:
    """The model classifier on texts the rules provably miss: excluded products and trick requests must be
    blocked, ordinary ones allowed. A case the rules already catch is reported as invalid, so this eval keeps
    exercising the classifier as the rules grow."""
    from venture_builder.policy_guard import PolicyGuard, check_rules
    from venture_builder.router import Router
    guard = PolicyGuard(Router.from_settings(settings))
    out = []
    for case in json.loads((ROOT / "evals" / "classifier_cases.json").read_text()):
        started = time.time()
        problems = []
        if check_rules(case["text"], case["context"]):
            problems.append("invalid case: the rules already catch it, so it doesn't test the classifier")
        res = guard.check(case["text"], context=case["context"])
        got = "allow" if res.allowed else "block"
        if res.rule not in (None, "classifier"):
            problems.append(f"decided by rule {res.rule}, not the classifier")
        if got != case["expect"]:
            problems.append(f"expected {case['expect']}, the classifier said {got} ({res.category}: {res.reason[:120]})")
        out.append({"id": case["id"], "context": case["context"], "expected": case["expect"], "got": got,
                    "category": res.category, "problems": problems, "seconds": round(time.time() - started)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the evals in evals/cases.json")
    ap.add_argument("--fake", action="store_true", help="use the test fakes instead of real models")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--save", action="store_true", help="write evals/results.md (real runs only)")
    args = ap.parse_args(argv)
    cases = json.loads((ROOT / "evals" / "cases.json").read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    from venture_builder.config import load_settings
    from venture_builder.runtime import Runtime
    env = dict(os.environ)
    env["RUN_MODE"] = "demo"
    if args.fake:
        sys.path.insert(0, str(ROOT / "tests"))
        from fakes import FakeModels, FakeSearch, fake_fetch  # noqa: E402
        from venture_builder.llm_clients import FakeClient
        from venture_builder.router import Router
        env.update({"RUNS_DIR": tempfile.mkdtemp(), "GEMINI_API_KEY": "fake", "OLLAMA_HOST": "http://127.0.0.1:9"})
        settings = load_settings(env, dotenv=None)

        def factory(s, brief):
            fake = FakeClient(FakeModels())
            return Runtime.new(s, brief, router=Router(s, oss=fake, gemma=fake), search=FakeSearch(), fetch=fake_fetch)
    else:
        settings = load_settings(env)

        def factory(s, brief):
            return Runtime.new(s, brief)

    results = []
    for case in cases:
        print(f"... {case['id']}", flush=True)
        try:
            results.append(run_case(case, settings, factory))
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a crashed harness
            results.append({"id": case["id"], "outcome": None, "expected": case["expect"]["gate1_in"],
                            "problems": [f"crashed: {type(exc).__name__}: {exc}"], "seconds": 0, "tokens": 0,
                            "run_id": None})
    if args.fake or (args.only and "classifier" not in args.only.split(",")):
        clf = None    # needs a real model; the fake classifier only recognises a test marker
    else:
        clf = run_classifier_cases(settings)
    passed = sum(1 for r in results if not r["problems"])
    lines = [f"# Eval results ({'fake models' if args.fake else 'real models, demo mode'})", "",
             f"{passed} of {len(results)} cases pass.", "", "| Case | Stage 1 | Expected | Result | Time | Tokens |",
             "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['outcome']} | {' / '.join(r['expected'])} | "
                     f"{'pass' if not r['problems'] else 'FAIL'} | {r['seconds']}s | {r['tokens']:,} |")
    for r in results:
        if r["problems"]:
            lines += ["", f"## {r['id']} (run {r['run_id']})", ""] + [f"- {p}" for p in r["problems"]]
    lines += ["", "## Policy classifier on texts the rules miss", ""]
    if clf is None:
        lines.append("Skipped: needs a real model (the fake classifier only recognises a test marker).")
    else:
        ok = sum(1 for r in clf if not r["problems"])
        lines += [f"{ok} of {len(clf)} cases pass.", "", "| Case | Context | Expected | Classifier | Category | Result |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['context']} | {r['expected']} | {r['got']} | {r['category']} | "
                  f"{'pass' if not r['problems'] else 'FAIL'} |" for r in clf]
        for r in clf:
            if r["problems"]:
                lines += ["", f"### {r['id']}", ""] + [f"- {p}" for p in r["problems"]]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.save and not args.fake:
        (ROOT / "evals" / "results.md").write_text(report)
    clf_ok = clf is None or all(not r["problems"] for r in clf)
    return 0 if passed == len(results) and clf_ok else 1


if __name__ == "__main__":
    sys.exit(main())
