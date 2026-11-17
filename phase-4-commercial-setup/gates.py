"""Phase 4 commercial gate. Deterministic: the policy reviewer's opinions are shown to the founder, but only
code checks decide the outcome.

  repeat  the pricing or the policy drafts fail a check (fix and re-check)
  pivot   even if every waitlist sign-up paid, the price would not cover the running costs
  go      everything checks out; the founder still has to approve
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateResult:
    outcome: str
    reasons: list[str] = field(default_factory=list)


def commercial_gate(pricing_problems: list[str], policy_issues: list[str], break_even: dict,
                    waitlist_signups: int | None) -> GateResult:
    problems = [f"pricing: {p}" for p in pricing_problems] + [f"policy: {i}" for i in policy_issues]
    if problems:
        return GateResult("repeat", problems)
    needed = break_even["needed"]
    if waitlist_signups is not None and needed > waitlist_signups:
        return GateResult("pivot", [
            f"Covering running costs needs {needed} {break_even['unit']}, but only {waitlist_signups} people joined "
            f"the waitlist. Even if every one of them paid, costs would not be covered. Raise the price, cut costs, "
            f"or go back to Phase 2 to grow demand."])
    reasons = ["Pricing and all three policy drafts pass every check."]
    if needed:
        reasons.append(f"Break-even: {needed} {break_even['unit']} (waitlist: "
                       f"{waitlist_signups if waitlist_signups is not None else 'unknown'}).")
    return GateResult("go", reasons)
