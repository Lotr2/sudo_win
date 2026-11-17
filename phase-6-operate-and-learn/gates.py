"""Phase 6 review gate: persevere / pivot / stop / repeat, from targets locked BEFORE the results.

The founder sets the targets with `targets`; they are hashed into the decision log, so they can't be moved after
seeing the numbers. The analyst model explains the outcome but cannot change it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


class TargetsTampered(Exception):
    pass


@dataclass
class GateResult:
    outcome: str  # persevere | pivot | stop | repeat
    reasons: list[str] = field(default_factory=list)


def targets_hash(t: dict) -> str:
    return hashlib.sha256(json.dumps(t, sort_keys=True).encode()).hexdigest()


def default_targets(break_even: int | None) -> dict:
    target = max(1, break_even or 1)
    return {"review_after_days": 30, "target_paying_customers": target,
            "stop_below_paying_customers": max(0, target // 2), "max_refund_rate": 0.2, "min_store_visitors": 100}


def validate_targets(t: dict) -> list[str]:
    problems = []
    if not 1 <= t["review_after_days"] <= 365:
        problems.append("review_after_days must be 1-365")
    if t["target_paying_customers"] < 1:
        problems.append("target_paying_customers must be at least 1")
    if not 0 <= t["stop_below_paying_customers"] < t["target_paying_customers"]:
        problems.append("stop_below_paying_customers must be at least 0 and below the target")
    if not 0 <= t["max_refund_rate"] <= 1:
        problems.append("max_refund_rate must be between 0 and 1")
    if t["min_store_visitors"] < 0:
        problems.append("min_store_visitors cannot be negative")
    return problems


def verify(locked: dict, approved_hash: str | None):
    if not approved_hash:
        raise TargetsTampered("No founder approval for these targets in the decision log. Run `targets` first.")
    if targets_hash(locked["targets"]) != approved_hash or locked.get("sha256") != approved_hash:
        raise TargetsTampered("The targets changed after the founder approved them.")


def review_gate(t: dict, m: dict) -> GateResult:
    paying, refund_rate = m["paying_customers"], m["refund_rate"] or 0.0
    target, stop_below = t["target_paying_customers"], t["stop_below_paying_customers"]
    seen = (f"{paying} paying customers (target {target}, stop line {stop_below}), refund rate "
            f"{refund_rate:.0%} (max {t['max_refund_rate']:.0%}), {m['store_visitors']} store visitors, "
            f"{m['days_open']} days open.")

    if paying >= target and refund_rate <= t["max_refund_rate"]:
        return GateResult("persevere", [f"Target reached: {seen}"])
    if m["days_open"] < t["review_after_days"]:
        return GateResult("repeat", [f"Review window not over ({m['days_open']} of {t['review_after_days']} days). "
                                     f"So far: {seen} No conclusion yet."])
    if m["store_visitors"] < t["min_store_visitors"]:
        return GateResult("repeat", [f"Only {m['store_visitors']} store visitors; at least {t['min_store_visitors']} "
                                     f"are needed to judge. Low traffic is not evidence either way: {seen}"])
    if refund_rate > t["max_refund_rate"] and m["ever_paid"]:
        outcome = "pivot" if paying >= stop_below and paying > 0 else "stop"
        return GateResult(outcome, [f"Refund rate too high: people buy but don't keep it. {seen}"])
    if paying >= stop_below and paying > 0:
        return GateResult("pivot", [f"Some paying customers but below target: {seen}"])
    return GateResult("stop", [f"Too few paying customers after the review window: {seen} The stop line was set "
                               f"before launch."])
