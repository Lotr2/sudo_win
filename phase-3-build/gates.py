"""Phase 3 build gate: the product ships only if the tests written BEFORE the code all pass.

Deterministic; no model judges the result. GO still needs the founder to try the product and approve it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agents.product_manager import criteria


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class GateResult:
    outcome: str  # go | repeat | stop
    checks: list[Check] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def ac_status(spec: dict, run) -> dict[str, str]:
    """Per acceptance criterion: 'pass' (has a passing test and no failing one), 'fail', or 'untested'."""
    status = {}
    for ac in criteria(spec):
        pat = re.compile(rf"^{re.escape(ac['id'])}\b")
        if any(pat.match(t) for t in run.failed):
            status[ac["id"]] = "fail"
        elif any(pat.match(t) for t in run.passed):
            status[ac["id"]] = "pass"
        else:
            status[ac["id"]] = "untested"
    return status


def build_gate(spec: dict, run, scans: dict[str, list[str]], docs_ok: bool) -> GateResult:
    if not spec["buildable"]:
        reason = spec.get("not_buildable_reason") or "the core value cannot be delivered as an offline web app"
        return GateResult("stop", [], [f"Not buildable with this builder: {reason}."])

    status = ac_status(spec, run)
    not_passing = [k for k, v in status.items() if v != "pass"]
    checks = [
        Check("Every acceptance criterion has a passing test", not not_passing,
              f"not passing: {', '.join(f'{k} ({status[k]})' for k in not_passing)}" if not_passing
              else f"{len(status)} of {len(status)} criteria pass"),
        Check("No failing, skipped or todo tests", run.ok,
              run.error or f"{len(run.passed)} passed, {len(run.failed)} failed, {run.skipped} skipped, {run.todo} todo"),
    ]
    for name, issues in scans.items():
        checks.append(Check(f"{name} passes safety and quality checks", not issues, "; ".join(issues) or "clean"))
    checks.append(Check("User guide written and passes copy rules", docs_ok))

    failed = [c for c in checks if not c.passed]
    if failed:
        return GateResult("repeat", checks, [f"{c.name}: {c.detail}" for c in failed])
    return GateResult("go", checks, ["All tests written before the code pass, and every check is clean."])
