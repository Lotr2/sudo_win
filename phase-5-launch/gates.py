"""Phase 5 launch gate. Deterministic: GO only when every launch piece is approved and the latest payment and
delivery test run passed every scenario. The founder then decides whether to open the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class GateResult:
    outcome: str  # go | repeat
    checks: list[Check] = field(default_factory=list)


def launch_gate(store_approved: tuple[bool, str], posts_reviewed: tuple[bool, str],
                delivery_rule: tuple[bool, str], test_run: dict | None, policies_present: tuple[bool, str]) -> GateResult:
    if test_run is None:
        run_check = Check("Payment and delivery test run passed", False, "no test run yet: run `test-run`")
    else:
        failed = [r["scenario"] for r in test_run["results"] if not r["passed"]]
        run_check = Check("Payment and delivery test run passed", test_run["passed"],
                          f"{len(test_run['results']) - len(failed)}/{len(test_run['results'])} scenarios pass"
                          + (f"; failing: {', '.join(failed)}" if failed else ""))
    checks = [Check("Store page approved by the founder", *store_approved),
              Check("Launch posts reviewed by the founder", *posts_reviewed),
              Check("Delivery rule approved by the founder", *delivery_rule),
              Check("Terms, privacy and refund policies present", *policies_present),
              run_check]
    return GateResult("go" if all(c.passed for c in checks) else "repeat", checks)
