"""Phase 2 demand gate. Deterministic: no model is involved in judging the evidence.

The thresholds come from the demand-test plan the founder approved BEFORE the test ran. The plan is locked by a
hash recorded in the decision log, so changing the targets after seeing results is detected and refused.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


class PlanTampered(Exception):
    pass


@dataclass
class GateResult:
    outcome: str  # go | repeat | pivot | stop
    reasons: list[str] = field(default_factory=list)
    days_left: float = 0.0


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verify_lock(locked: dict, approved_hash: str | None):
    """`locked` is the saved plan file; `approved_hash` is the hash the founder approved (from the decision log)."""
    actual = plan_hash(locked["plan"])
    if not approved_hash:
        raise PlanTampered("No founder approval for this demand-test plan was found in the decision log.")
    if actual != locked.get("sha256") or actual != approved_hash:
        raise PlanTampered("The demand-test plan changed after the founder approved it. "
                           "Targets cannot be moved after the test starts.")


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def demand_gate(locked: dict, evidence: dict, now: datetime | None = None) -> GateResult:
    plan = locked["plan"]
    now = now or datetime.now(timezone.utc)
    end = parse_time(locked["locked_at"]) + timedelta(days=plan["duration_days"])
    days_left = max(0.0, (end - now).total_seconds() / 86400)
    v, s = evidence["visitors"], evidence["signups"]
    target, stop_below, min_v = plan["target_signups"], plan["stop_below_signups"], plan["min_visitors"]
    rate = f" ({s / v:.1%} of visitors)" if v else ""
    seen = f"{s} sign-ups from {v} visitors{rate}."
    src = [] if evidence.get("verified") else [f"Evidence is founder-reported from {evidence.get('source') or 'an unnamed source'} and was not checked by the system."]

    if s >= target:
        return GateResult("go", [f"Target reached: {seen} Target was {target}."] + src, days_left)
    if days_left > 0:
        return GateResult("repeat", [f"Test still running ({days_left:.1f} days left). So far: {seen} "
                                     f"Target is {target}; no conclusion until the test ends."] + src, days_left)
    if v < min_v:
        return GateResult("repeat", [f"Only {v} visitors; the plan needs at least {min_v} to judge demand. "
                                     f"{seen} Low traffic is not evidence either way: get the page in front of "
                                     f"more early adopters, then run the gate again."] + src, days_left)
    if s >= stop_below:
        return GateResult("pivot", [f"Some interest but below target: {seen} Target was {target}, stop line "
                                    f"{stop_below}. Change the customer, message or offer and test again."] + src,
                          days_left)
    return GateResult("stop", [f"Demand too weak: {seen} The stop line set before the test was {stop_below} "
                               f"(target {target}). The system recommends STOP rather than building."] + src,
                      days_left)
