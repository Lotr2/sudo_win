"""Deterministic phase gates. Same thresholds for every variant.

A gate RECOMMENDS go / repeat / pivot / stop from pre-set rules (logged with model=null,
approved_by=null). The founder then accepts or overrides it through approval.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Pre-set evidence thresholds (identical across variants; change only via the owner).
MIN_VERIFIED_PAIN_SIGNALS = 1          # phase 1
MIN_INDEPENDENT_DEMAND_SOURCES = 3     # phase 2
MAX_ASSUMPTION_RATIO = 0.5             # phase 2
PRICE_RANGE_FACTOR = (0.5, 2.0)        # phase 4: price within [0.5 x min, 2 x max] of sourced competitor prices


@dataclass
class GateResult:
    phase: int
    outcome: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------- founder choices at a gate (stage review)
# revise = re-run only the failed steps with the failure reasons, a compressed previous answer and the founder's note
# redo   = re-run the whole phase from scratch (new sampling seed)
# reset  = phase 5 only: clear the MOCKED commerce state, then re-run the phase
RETRY_FINALS = ("revise", "redo", "reset")
_RETRIES_BY_PHASE = {1: ("revise", "redo"), 2: ("revise", "redo"), 3: ("revise", "redo"), 4: ("revise", "redo"),
                     5: ("reset",), 6: ()}          # gate 6: re-running cannot create real customers -> founder decides


def resolve(phase: int, choice: str, recommended: str) -> str:
    """Founder choice -> final: accept follows the recommendation; 'repeat' maps to this gate's retry kind."""
    if choice == "accept":
        choice = recommended
    if choice == "repeat":
        choice = {5: "reset", 6: "repeat"}.get(phase, "revise")    # gate 6 'repeat' ends the run: collect evidence
    return choice


def choices(phase: int, recommended: str, retries_left: bool) -> list[str]:
    """What the founder may choose at this gate right now (the cap removes the retry options)."""
    retry = list(_RETRIES_BY_PHASE[phase]) if retries_left else []
    allowed = ["go", "pivot", "stop"] + retry
    out = [c for c in ("accept", "repeat") if resolve(phase, c, recommended) in allowed + (["repeat"] if phase == 6 else [])]
    return out + allowed


def phase1(*, excluded: list[str], verified_pain: int) -> GateResult:
    m = {"excluded_categories": excluded, "verified_pain_signals": verified_pain}
    if excluded:
        return GateResult(1, "stop", [f"Excluded product category: {', '.join(excluded)}"], m)
    if verified_pain < MIN_VERIFIED_PAIN_SIGNALS:
        return GateResult(1, "pivot", [f"Verified pain signals {verified_pain} < {MIN_VERIFIED_PAIN_SIGNALS}"], m)
    return GateResult(1, "go", ["Category allowed and at least one verified pain signal"], m)


def phase2(*, independent_sources: int, assumption_ratio: float, demand_result: dict | None) -> GateResult:
    m = {"independent_verified_sources": independent_sources, "assumption_ratio": round(assumption_ratio, 2),
         "demand_result": demand_result or "not collected"}
    reasons = []
    if independent_sources < MIN_INDEPENDENT_DEMAND_SOURCES:
        reasons.append(f"Independent verified sources {independent_sources} < {MIN_INDEPENDENT_DEMAND_SOURCES}")
    if assumption_ratio > MAX_ASSUMPTION_RATIO:
        reasons.append(f"Assumption ratio {assumption_ratio:.2f} > {MAX_ASSUMPTION_RATIO}")
    if demand_result and demand_result.get("actual", 0) < demand_result.get("target", 0):
        reasons.append(f"Demand test {demand_result.get('actual')} < target {demand_result.get('target')}")
    if reasons:
        return GateResult(2, "stop", reasons + ["Evidence is weak: the system recommends not building this."], m)
    note = "Desk evidence only: no real demand-test results were collected." if not demand_result else "Demand test met target."
    return GateResult(2, "go", ["All evidence thresholds met", note], m)


def phase3(*, tests_passed: bool, failures: list[str]) -> GateResult:
    m = {"tests_passed": tests_passed, "failures": failures}
    return GateResult(3, "go" if tests_passed else "repeat", ["MVP tests passed"] if tests_passed else failures, m)


def phase4(*, price: float | None, competitor_prices: list[float], policies_present: bool) -> GateResult:
    m = {"price_usd": price, "competitor_prices": competitor_prices, "policies_present": policies_present}
    reasons = []
    if not price or price <= 0:
        reasons.append("No valid price")
    elif competitor_prices:
        lo, hi = min(competitor_prices) * PRICE_RANGE_FACTOR[0], max(competitor_prices) * PRICE_RANGE_FACTOR[1]
        if not lo <= price <= hi:
            reasons.append(f"Price {price} outside sourced range [{lo:.2f}, {hi:.2f}]")
    if not policies_present:
        reasons.append("Missing terms, privacy or refund policy")
    if reasons:
        return GateResult(4, "repeat", reasons, m)
    extra = [] if competitor_prices else ["No sourced competitor prices: price is an ASSUMPTION"]
    return GateResult(4, "go", ["Price and draft policies ready"] + extra, m)


def phase5(*, invariants: dict[str, bool]) -> GateResult:
    failed = [k for k, ok in invariants.items() if not ok]
    return GateResult(5, "repeat" if failed else "go",
                      [f"Invariant failed: {k}" for k in failed] or ["All sale-to-cash invariants hold (MOCKED)"],
                      {"invariants": invariants})


def phase6(*, real_customer_evidence: bool) -> GateResult:
    if real_customer_evidence:
        return GateResult(6, "go", ["Real customer evidence exists"], {"real_customer_evidence": True})
    return GateResult(6, "repeat", ["Only MOCKED test orders: collect real customer evidence before scaling"],
                      {"real_customer_evidence": False})
