"""Gates 1-6 as pure functions of the dossier and thresholds fixed in advance (ARCHITECTURE.md → Gates).

Each returns a GateResult (go / repeat / pivot / stop) with reasons; none calls a model. A tool failure
is not evidence of absence: Gate 1 returns repeat, never go and never a stop based on missing data.
"""

from __future__ import annotations

import hashlib
import json

from .decision_log import logged
from .schemas import GateResult, InvestorReview, VariantScore


class ThresholdTamperError(RuntimeError):
    """The locked targets no longer match the hash written before results existed."""


def lock_hash(targets: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(targets, sort_keys=True).encode()).hexdigest()


@logged("gate", "gate1_discover")
def gate1(scores: list[VariantScore], *, excluded: str | None, search_failed: bool, repeats: int,
          t: dict) -> GateResult:
    g, limits = t["gate1"], t["limits"]
    if excluded:
        return GateResult(gate=1, outcome="stop", reasons=[f"Excluded product category: {excluded}."])
    if search_failed:
        return GateResult(gate=1, outcome="repeat", reasons=[
            "Search tools failed, so market claims fell back to ASSUMPTION. A tool failure is not evidence "
            "of absence: repeat the research when search is reachable."])
    if not scores:
        return GateResult(gate=1, outcome="repeat" if repeats < limits["max_repeats_per_phase"] else "stop",
                          reasons=["No idea variants were produced."])

    def passes(s: VariantScore) -> bool:
        return (s.pain_signals >= g["min_pain_signals"] and s.pain_domains >= g["min_source_domains"]
                and s.score >= g["min_score"] and s.fit_rules_pass)

    ranked = sorted(scores, key=lambda s: s.score, reverse=True)
    top = ranked[0]
    detail = {"top_variant": top.variant_id, "score": round(top.score, 3), "pain_signals": top.pain_signals,
              "pain_domains": top.pain_domains, "fit_rules_pass": top.fit_rules_pass}
    if passes(top):
        return GateResult(gate=1, outcome="go", details=detail, reasons=[
            f"Top variant {top.variant_id}: {top.pain_signals} sourced pain signals from {top.pain_domains} domains, "
            f"score {top.score:.2f} ≥ {g['min_score']}, founder-fit rules pass."])
    other = next((s for s in ranked[1:] if passes(s)), None)
    if other:
        detail["passing_variant"] = other.variant_id
        return GateResult(gate=1, outcome="pivot", details=detail, reasons=[
            f"Top-scoring variant {top.variant_id} misses a threshold, but variant {other.variant_id} passes "
            f"({other.pain_signals} signals, {other.pain_domains} domains, score {other.score:.2f})."])
    can_repeat = repeats < limits["max_repeats_per_phase"]
    if top.pain_signals == 0:
        if can_repeat:
            return GateResult(gate=1, outcome="repeat", details=detail, reasons=[
                "No sourced pain signal for any variant yet; repeat with more evidence."])
        return GateResult(gate=1, outcome="stop", details=detail, reasons=[
            f"No sourced pain signal after {repeats} repeats. The only support is assumed or simulated evidence."])
    why = []
    if top.pain_signals < g["min_pain_signals"]:
        why.append(f"only {top.pain_signals} sourced pain signals (need {g['min_pain_signals']})")
    if top.pain_domains < g["min_source_domains"]:
        why.append(f"signals from {top.pain_domains} domains (need {g['min_source_domains']})")
    if top.score < g["min_score"]:
        why.append(f"score {top.score:.2f} below {g['min_score']}")
    if not top.fit_rules_pass:
        why.append("founder-fit rules fail (budget, hours or skills)")
    if can_repeat:
        return GateResult(gate=1, outcome="repeat", details=detail, reasons=[f"Variant {top.variant_id}: " + "; ".join(why) + "."])
    return GateResult(gate=1, outcome="stop", details=detail, reasons=[
        f"Variant {top.variant_id}: " + "; ".join(why) + f", still after {repeats} repeats."])


@logged("gate", "gate2_validate")
def gate2(review: InvestorReview, *, locked: dict, recorded_hash: str, result: float, mocked: bool,
          repeats: int, fatal_answered: bool, alternative_supported: bool, t: dict) -> GateResult:
    if lock_hash(locked) != recorded_hash:
        raise ThresholdTamperError("locked targets changed after locking; the result is rejected")
    g = t["gate2"]
    target = float(locked["target"])
    ratio = result / target if target else 0.0
    fatal = [r.assumption for r in review.risks if r.fatal]
    detail = {"target": target, "result": result, "ratio": round(ratio, 3), "metric": locked.get("metric"),
              "fatal_risks": fatal}
    if fatal and not fatal_answered:
        return GateResult(gate=2, outcome="stop", mocked_evidence=mocked, details=detail,
                          reasons=[f"Fatal risk with no answer: {fatal[0]}"])
    if ratio >= g["go_ratio"]:
        return GateResult(gate=2, outcome="go", mocked_evidence=mocked, details=detail,
                          reasons=[f"Result {result:g} reached {ratio:.0%} of the locked target {target:g}."])
    if ratio >= g["repeat_ratio"]:
        if repeats < 1:
            return GateResult(gate=2, outcome="repeat", mocked_evidence=mocked, details=detail,
                              reasons=[f"Result {result:g} is {ratio:.0%} of the target {target:g} (50-99%): repeat once."])
        return GateResult(gate=2, outcome="pivot" if alternative_supported else "stop", mocked_evidence=mocked,
                          details=detail, reasons=[f"Result still {ratio:.0%} of target after a repeat."])
    if alternative_supported:
        return GateResult(gate=2, outcome="pivot", mocked_evidence=mocked, details=detail, reasons=[
            f"Result {result:g} is {ratio:.0%} of the target {target:g}; a different segment is supported by the evidence."])
    return GateResult(gate=2, outcome="stop", mocked_evidence=mocked, details=detail, reasons=[
        f"Result {result:g} is {ratio:.0%} of the locked target {target:g}: missed by more than half. "
        "Stop or pivot rather than reframing the number."])


@logged("gate", "gate3_build")
def gate3(*, tests_passed: int, tests_total: int, loops_used: int, repeated: bool,
          promise_unmeetable: bool, t: dict) -> GateResult:
    detail = {"passed": tests_passed, "total": tests_total, "loops": loops_used}
    if promise_unmeetable:
        return GateResult(gate=3, outcome="pivot", details=detail,
                          reasons=["The spec cannot meet the landing-page promise with a supported template."])
    if tests_total and tests_passed == tests_total:
        return GateResult(gate=3, outcome="go", details=detail,
                          reasons=[f"All {tests_total} acceptance tests pass after {loops_used} build loop(s)."])
    if not repeated:
        return GateResult(gate=3, outcome="repeat", details=detail, reasons=[
            f"{tests_total - tests_passed} of {tests_total} acceptance tests still fail after {loops_used} loops: "
            "repeat once with a smaller spec."])
    return GateResult(gate=3, outcome="stop", details=detail,
                      reasons=[f"Acceptance tests still fail after the repeat ({tests_passed}/{tests_total})."])


@logged("gate", "gate4_commercial")
def gate4(*, violations: list[str], price: float, price_range: tuple[float, float] | None,
          price_justified: bool, t: dict) -> GateResult:
    detail = {"violations": violations, "price": price, "sourced_range": price_range}
    if violations:
        return GateResult(gate=4, outcome="repeat", details=detail,
                          reasons=[f"{len(violations)} compliance violation(s) remain."])
    if price_range and not (price_range[0] <= price <= price_range[1]) and not price_justified:
        return GateResult(gate=4, outcome="repeat", details=detail, reasons=[
            f"Price {price:g} is outside the sourced competitor range {price_range[0]:g}-{price_range[1]:g} without justification."])
    reason = (f"Linter clean; price {price:g} within the sourced range {price_range[0]:g}-{price_range[1]:g}."
              if price_range else "Linter clean; no sourced competitor prices, so the price reasoning is labelled ASSUMPTION.")
    return GateResult(gate=4, outcome="go", details=detail, reasons=[reason])


@logged("gate", "gate5_launch")
def gate5(*, items: list[dict], delivery_tests_passed: bool, withdrawn: bool, t: dict) -> GateResult:
    approved = [i for i in items if i["status"] == "published"]
    rejected = [i for i in items if i["status"] == "rejected"]
    pending = [i for i in items if i["status"] not in {"published", "rejected"}]
    detail = {"published": len(approved), "rejected": len(rejected), "pending": len(pending),
              "delivery_tests_passed": delivery_tests_passed}
    if withdrawn:
        return GateResult(gate=5, outcome="stop", details=detail, reasons=["The founder withdrew the launch."])
    if pending or rejected or not delivery_tests_passed:
        why = []
        if pending:
            why.append(f"{len(pending)} item(s) not yet approved")
        if rejected:
            why.append(f"{len(rejected)} rejected item(s) to revise")
        if not delivery_tests_passed:
            why.append("delivery, refund or idempotency tests failed")
        return GateResult(gate=5, outcome="repeat", details=detail, reasons=["; ".join(why) + "."])
    return GateResult(gate=5, outcome="go", mocked_evidence=True, details=detail, reasons=[
        f"All {len(approved)} outbound item(s) approved and executed (publishing MOCKED); delivery, refund and "
        "idempotency tests pass (payments MOCKED)."])


@logged("gate", "gate6_operate")
def gate6(*, metrics: dict, targets: dict, memo_passes: bool, alternative_supported: bool, t: dict) -> GateResult:
    misses = []
    if metrics.get("conversion", 0) < targets["min_conversion"]:
        misses.append(f"conversion {metrics.get('conversion', 0):.1%} < {targets['min_conversion']:.1%}")
    if metrics.get("refund_rate", 0) > targets["max_refund_rate"]:
        misses.append(f"refund rate {metrics.get('refund_rate', 0):.1%} > {targets['max_refund_rate']:.1%}")
    detail = {"metrics": metrics, "targets": targets, "misses": misses}
    if not memo_passes:
        return GateResult(gate=6, outcome="repeat", mocked_evidence=True, details=detail,
                          reasons=["The memo fails the critic's rubric: its claims don't match the data."])
    if not misses:
        return GateResult(gate=6, outcome="go", mocked_evidence=True, details=detail,
                          reasons=["Metrics meet the locked targets: persevere."])
    if alternative_supported:
        return GateResult(gate=6, outcome="pivot", mocked_evidence=True, details=detail,
                          reasons=["Metrics miss targets (" + "; ".join(misses) + "), and a sourced alternative exists."])
    return GateResult(gate=6, outcome="stop", mocked_evidence=True, details=detail,
                      reasons=["Metrics miss targets (" + "; ".join(misses) + ") and no alternative is supported."])
