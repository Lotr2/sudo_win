"""Gates 1-6 as pure functions of the dossier and thresholds fixed in advance (ARCHITECTURE.md → Gates).

Each returns a GateResult (go / repeat / pivot / stop) with reasons; none calls a model. A tool failure
is not evidence of absence: Gate 1 returns repeat, never go and never a stop based on missing data.
"""

from __future__ import annotations

import hashlib
import json

from . import labels
from .decision_log import logged
from .schemas import GateResult, InvestorReview, VariantScore


class ThresholdTamperError(RuntimeError):
    """The locked targets no longer match the hash written before results existed."""


def lock_hash(targets: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(targets, sort_keys=True).encode()).hexdigest()


@logged("gate", "gate1_discover")
def gate1(scores: list[VariantScore], *, excluded: str | None, search_failed: bool, repeats: int,
          t: dict, names: dict | None = None) -> GateResult:
    g, limits = t["gate1"], t["limits"]
    names = names or {}

    def nm(vid: str) -> str:
        return f"\"{names[vid]}\"" if vid in names else f"version {vid}"

    if excluded:
        return GateResult(gate=1, outcome="stop", reasons=[
            f"This idea falls under {excluded}, which the team doesn't help with: these products can cause real harm "
            "if the advice is wrong, and they need licensed experts. Consider a related idea outside that area."])
    if search_failed:
        return GateResult(gate=1, outcome="repeat", reasons=[
            "Web search wasn't working, so the team couldn't check the market and everything is still a guess "
            "(ASSUMPTION). That says nothing bad about your idea: try again once search is back."])
    if not scores:
        return GateResult(gate=1, outcome="repeat" if repeats < limits["max_repeats_per_phase"] else "stop",
                          reasons=["The team couldn't come up with versions of the idea to test."])

    def passes(s: VariantScore) -> bool:
        return (s.pain_signals >= g["min_pain_signals"] and s.pain_domains >= g["min_source_domains"]
                and s.score >= g["min_score"] and s.fit_rules_pass)

    ranked = sorted(scores, key=lambda s: s.score, reverse=True)
    top = ranked[0]
    detail = {"top_variant": top.variant_id, "best_version": names.get(top.variant_id, top.variant_id),
              "score": round(top.score, 3),
              "pain_signals": top.pain_signals,
              "pain_domains": top.pain_domains, "fit_rules_pass": top.fit_rules_pass}
    if passes(top):
        return GateResult(gate=1, outcome="go", details=detail, reasons=[
            f"The best version, {nm(top.variant_id)}, looks promising: "
            f"the team found {labels.plural(top.pain_signals, 'real example')} of people struggling with this problem "
            f"on {labels.plural(top.pain_domains, 'different website')}, it scores {top.score * 100:.0f} out of 100 (needs "
            f"{g['min_score'] * 100:.0f}), and it fits your skills, budget and time."])
    other = next((s for s in ranked[1:] if passes(s)), None)
    if other:
        detail["passing_variant"] = other.variant_id
        detail["passing_version"] = names.get(other.variant_id, other.variant_id)
        return GateResult(gate=1, outcome="pivot", details=detail, reasons=[
            f"The top-scoring version, {nm(top.variant_id)}, falls short, but {nm(other.variant_id)} passes: "
            f"{labels.plural(other.pain_signals, 'real example')} of the problem on "
            f"{labels.plural(other.pain_domains, 'website')}, scoring "
            f"{other.score * 100:.0f} out of 100. Switching to it is the better bet."])
    can_repeat = repeats < limits["max_repeats_per_phase"]
    if top.pain_signals == 0:
        if can_repeat:
            return GateResult(gate=1, outcome="repeat", details=detail, reasons=[
                "The team hasn't yet found real people online describing this problem, so the need is still a "
                "guess. Another round of research, ideally with a narrower customer group, may find it."])
        return GateResult(gate=1, outcome="stop", details=detail, reasons=[
            f"After {repeats} extra rounds of research, the team still found no real people describing this "
            "problem. Without that, building it would be a gamble."])
    why = []
    if top.pain_signals < g["min_pain_signals"]:
        why.append(f"only {labels.plural(top.pain_signals, 'real example')} of people with this problem "
                   f"(needs {g['min_pain_signals']})")
    if top.pain_domains < g["min_source_domains"]:
        why.append(f"examples from only {labels.plural(top.pain_domains, 'website')} (needs {g['min_source_domains']})")
    if top.score < g["min_score"]:
        why.append(f"a score of {top.score * 100:.0f} out of 100 (needs {g['min_score'] * 100:.0f})")
    if not top.fit_rules_pass:
        why.append("it doesn't fit your budget, time or skills well enough")
    if can_repeat:
        return GateResult(gate=1, outcome="repeat", details=detail, reasons=[
            f"The best version so far, {nm(top.variant_id)}, isn't there yet: " + "; ".join(why) + "."])
    return GateResult(gate=1, outcome="stop", details=detail, reasons=[
        f"Even after {repeats} extra rounds, the best version, {nm(top.variant_id)}, falls short: " + "; ".join(why) + "."])


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
                          reasons=[f"There's a risk that could sink the business, and it hasn't been answered yet: {fatal[0]}"])
    if ratio >= g["go_ratio"]:
        return GateResult(gate=2, outcome="go", mocked_evidence=mocked, details=detail,
                          reasons=[f"The demand test reached {result:g}, which is {ratio:.0%} of the target of {target:g} set in advance."])
    if ratio >= g["repeat_ratio"]:
        if repeats < 1:
            return GateResult(gate=2, outcome="repeat", mocked_evidence=mocked, details=detail,
                              reasons=[f"The demand test reached {result:g}, which is {ratio:.0%} of the target of {target:g}. That's "
                                       "close, so it's worth running the test once more."])
        return GateResult(gate=2, outcome="pivot" if alternative_supported else "stop", mocked_evidence=mocked,
                          details=detail, reasons=[f"After a second try the test still only reached {ratio:.0%} of the target."])
    if alternative_supported:
        return GateResult(gate=2, outcome="pivot", mocked_evidence=mocked, details=detail, reasons=[
            f"The demand test reached {result:g}, only {ratio:.0%} of the target of {target:g}. But the research "
            "points to a different customer group that may want it more."])
    return GateResult(gate=2, outcome="stop", mocked_evidence=mocked, details=detail, reasons=[
        f"The demand test reached {result:g}, only {ratio:.0%} of the target of {target:g} set in advance: less "
        "than half. It's better to stop or change direction than to move the goalposts."])


@logged("gate", "gate3_build")
def gate3(*, tests_passed: int, tests_total: int, loops_used: int, repeated: bool,
          promise_unmeetable: bool, t: dict) -> GateResult:
    detail = {"passed": tests_passed, "total": tests_total, "loops": loops_used}
    if promise_unmeetable:
        return GateResult(gate=3, outcome="pivot", details=detail,
                          reasons=["The product as planned can't deliver what the landing page promises with the formats the team can build."])
    if tests_total and tests_passed == tests_total:
        return GateResult(gate=3, outcome="go", details=detail,
                          reasons=[f"The product passes all {tests_total} quality checks (after {loops_used} build round(s))."])
    if not repeated:
        return GateResult(gate=3, outcome="repeat", details=detail, reasons=[
            f"{tests_total - tests_passed} of {tests_total} quality checks still fail after {loops_used} build rounds. "
            "Trying once more with a smaller first version usually fixes this."])
    return GateResult(gate=3, outcome="stop", details=detail,
                      reasons=[f"Even after a smaller retry, only {tests_passed} of {tests_total} quality checks pass."])


@logged("gate", "gate4_commercial")
def gate4(*, violations: list[str], price: float, price_range: tuple[float, float] | None,
          price_justified: bool, t: dict) -> GateResult:
    detail = {"violations": violations, "price": price, "sourced_range": price_range}
    if violations:
        return GateResult(gate=4, outcome="repeat", details=detail, reasons=[
            f"{labels.plural(len(violations), 'wording problem')} still need fixing before launch: "
            + "; ".join(labels.violation_text(v) for v in violations[:3]) + "."])
    if price_range and not (price_range[0] <= price <= price_range[1]) and not price_justified:
        return GateResult(gate=4, outcome="repeat", details=detail, reasons=[
            f"The price of ${price:g} is outside what similar products charge (${price_range[0]:g} to "
            f"${price_range[1]:g}), and there's no clear reason why."])
    reason = (f"The texts are clean, and the price of ${price:g} is in line with similar products "
              f"(${price_range[0]:g} to ${price_range[1]:g})." if price_range else
              "The texts are clean. The team couldn't find competitor prices, so the price is a reasoned guess (ASSUMPTION).")
    return GateResult(gate=4, outcome="go", details=detail, reasons=[reason])


@logged("gate", "gate5_launch")
def gate5(*, items: list[dict], delivery_tests_passed: bool, withdrawn: bool, t: dict) -> GateResult:
    approved = [i for i in items if i["status"] == "published"]
    rejected = [i for i in items if i["status"] == "rejected"]
    pending = [i for i in items if i["status"] not in {"published", "rejected"}]
    detail = {"published": len(approved), "rejected": len(rejected), "pending": len(pending),
              "delivery_tests_passed": delivery_tests_passed}
    if withdrawn:
        return GateResult(gate=5, outcome="stop", details=detail, reasons=["You withdrew the launch."])
    if pending or rejected or not delivery_tests_passed:
        why = []
        if pending:
            why.append(f"{len(pending)} item(s) still waiting for your approval")
        if rejected:
            why.append(f"{len(rejected)} item(s) you asked to change need revising")
        if not delivery_tests_passed:
            why.append("the practice sale, delivery or refund checks didn't all pass")
        return GateResult(gate=5, outcome="repeat", details=detail, reasons=[("; ".join(why) + ".").capitalize()])
    return GateResult(gate=5, outcome="go", mocked_evidence=True, details=detail, reasons=[
        f"All {len(approved)} item(s) you approved were recorded (publishing MOCKED: nothing went online), and the "
        "practice sale, delivery and refund all worked (payments MOCKED)."])


@logged("gate", "gate6_operate")
def gate6(*, metrics: dict, targets: dict, memo_passes: bool, alternative_supported: bool, t: dict,
          mocked: bool = True) -> GateResult:
    misses = []
    if metrics.get("conversion", 0) < targets["min_conversion"]:
        misses.append(f"only {metrics.get('conversion', 0):.1%} of visitors bought (target {targets['min_conversion']:.1%})")
    if metrics.get("refund_rate", 0) > targets["max_refund_rate"]:
        misses.append(f"{metrics.get('refund_rate', 0):.1%} of buyers asked for a refund (limit {targets['max_refund_rate']:.1%})")
    detail = {"metrics": metrics, "targets": targets, "misses": misses}
    if not memo_passes:
        return GateResult(gate=6, outcome="repeat", mocked_evidence=mocked, details=detail,
                          reasons=["The review of results didn't match the numbers, so it's being rewritten before you decide."])
    if not misses:
        return GateResult(gate=6, outcome="go", mocked_evidence=mocked, details=detail,
                          reasons=["The results meet the targets set in advance" + (" (practice data, MOCKED)" if mocked else "")
                                   + ": keep going."])
    if alternative_supported:
        return GateResult(gate=6, outcome="pivot", mocked_evidence=mocked, details=detail,
                          reasons=["The results miss the targets (" + "; ".join(misses) + "), but the research points to a promising alternative."])
    return GateResult(gate=6, outcome="stop", mocked_evidence=mocked, details=detail,
                      reasons=["The results miss the targets (" + "; ".join(misses) + "), and no alternative is backed by evidence."])
