"""Gates 1-6 as pure functions of the dossier and thresholds fixed in advance (ARCHITECTURE.md → Gates).

Each gate decides only: it returns an outcome (go / repeat / pivot / stop) plus reason codes and the facts
behind them. None calls a model, and none writes prose: `labels.gate_reason` turns each code and its facts
into the sentence the founder reads, so the decision and its wording can change independently.

- Repeat limits are per gate in `config/thresholds.toml` (`[gateN] max_repeats`, falling back to
  `[limits] max_repeats_per_phase`).
- Targets fixed before results exist (Gate 2's experiment target, Gate 6's goals) arrive as `LockedTargets`
  and are verified against their hash before use.
- A tool failure is not evidence of absence: Gate 1 returns repeat, never go and never a stop based on
  missing data.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from . import labels
from .decision_log import logged
from .schemas import GateResult, InvestorReview, VariantScore


class ThresholdTamperError(RuntimeError):
    """Locked targets or thresholds no longer match the hash written before results existed."""


def lock_hash(targets: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(targets, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class LockedTargets:
    """Targets fixed (and hashed into the decision log) before any result exists."""
    targets: dict
    hash: str

    @classmethod
    def lock(cls, targets: dict) -> "LockedTargets":
        frozen = copy.deepcopy(dict(targets))
        return cls(frozen, lock_hash(frozen))

    def verify(self, what: str = "locked targets") -> dict:
        """The targets, if they still match their hash; otherwise the result is rejected."""
        if lock_hash(self.targets) != self.hash:
            raise ThresholdTamperError(f"{what} changed after locking; the result is rejected")
        return self.targets


def max_repeats(t: dict, gate: int) -> int:
    return int(t.get(f"gate{gate}", {}).get("max_repeats", t["limits"]["max_repeats_per_phase"]))


def _result(gate: int, outcome: str, code: str, facts: dict, details: dict | None = None,
            mocked: bool = False) -> GateResult:
    return GateResult(gate=gate, outcome=outcome, codes=[code], reasons=[labels.gate_reason(code, facts)],
                      details=details or {}, mocked_evidence=mocked)


@logged("gate", "gate1_discover")
def gate1(scores: list[VariantScore], *, excluded: str | None, search_failed: bool, repeats: int,
          t: dict, names: dict | None = None) -> GateResult:
    g = t["gate1"]
    names = names or {}
    can_repeat = repeats < max_repeats(t, 1)

    if excluded:
        return _result(1, "stop", "excluded_category", {"category": excluded})
    if search_failed:
        return _result(1, "repeat", "search_failed", {})
    if not scores:
        return _result(1, "repeat" if can_repeat else "stop", "no_versions", {})

    def passes(s: VariantScore) -> bool:
        return (s.pain_signals >= g["min_pain_signals"] and s.pain_domains >= g["min_source_domains"]
                and s.score >= g["min_score"] and s.fit_rules_pass)

    def version(s: VariantScore) -> dict:
        return {"name": names.get(s.variant_id), "id": s.variant_id, "score": s.score,
                "pain_signals": s.pain_signals, "pain_domains": s.pain_domains}

    ranked = sorted(scores, key=lambda s: s.score, reverse=True)
    top = ranked[0]
    detail = {"top_variant": top.variant_id, "best_version": names.get(top.variant_id, top.variant_id),
              "score": round(top.score, 3),
              "pain_signals": top.pain_signals,
              "pain_domains": top.pain_domains, "fit_rules_pass": top.fit_rules_pass}
    facts = {"top": version(top), "min_score": g["min_score"], "repeats": repeats}
    if passes(top):
        return _result(1, "go", "top_version_passes", facts, detail)
    other = next((s for s in ranked[1:] if passes(s)), None)
    if other:
        detail["passing_variant"] = other.variant_id
        detail["passing_version"] = names.get(other.variant_id, other.variant_id)
        return _result(1, "pivot", "other_version_passes", facts | {"other": version(other)}, detail)
    if top.pain_signals == 0:
        return _result(1, "repeat" if can_repeat else "stop", "no_pain_found" if can_repeat else "no_pain_after_repeats",
                       facts, detail)
    shortfalls = []
    if top.pain_signals < g["min_pain_signals"]:
        shortfalls.append({"kind": "pain_signals", "have": top.pain_signals, "need": g["min_pain_signals"]})
    if top.pain_domains < g["min_source_domains"]:
        shortfalls.append({"kind": "pain_domains", "have": top.pain_domains, "need": g["min_source_domains"]})
    if top.score < g["min_score"]:
        shortfalls.append({"kind": "score", "have": top.score, "need": g["min_score"]})
    if not top.fit_rules_pass:
        shortfalls.append({"kind": "fit"})
    facts["shortfalls"] = shortfalls
    return _result(1, "repeat" if can_repeat else "stop", "below_bar" if can_repeat else "below_bar_after_repeats",
                   facts, detail)


@logged("gate", "gate2_validate")
def gate2(review: InvestorReview, *, locked: LockedTargets, result: float, mocked: bool,
          repeats: int, fatal_answered: bool, alternative_supported: bool, t: dict) -> GateResult:
    targets = locked.verify("Gate 2 targets")
    g = t["gate2"]
    target = float(targets["target"])
    ratio = result / target if target else 0.0
    fatal = [r.assumption for r in review.risks if r.fatal]
    detail = {"target": target, "result": result, "ratio": round(ratio, 3), "metric": targets.get("metric"),
              "fatal_risks": fatal}
    facts = {"target": target, "result": result, "ratio": ratio}
    if fatal and not fatal_answered:
        return _result(2, "stop", "fatal_risk_unanswered", {"risk": fatal[0]}, detail, mocked)
    if ratio >= g["go_ratio"]:
        return _result(2, "go", "target_met", facts, detail, mocked)
    if ratio >= g["repeat_ratio"]:
        if repeats < max_repeats(t, 2):
            return _result(2, "repeat", "close_to_target", facts, detail, mocked)
        return _result(2, "pivot" if alternative_supported else "stop", "close_after_retry", facts, detail, mocked)
    if alternative_supported:
        return _result(2, "pivot", "far_below_target_alternative", facts, detail, mocked)
    return _result(2, "stop", "far_below_target", facts, detail, mocked)


@logged("gate", "gate3_build")
def gate3(*, tests_passed: int, tests_total: int, loops_used: int, repeats: int,
          promise_unmeetable: bool, t: dict) -> GateResult:
    detail = {"passed": tests_passed, "total": tests_total, "loops": loops_used}
    facts = dict(detail)
    if promise_unmeetable:
        return _result(3, "pivot", "promise_unmeetable", facts, detail)
    if tests_total and tests_passed == tests_total:
        return _result(3, "go", "all_tests_pass", facts, detail)
    if repeats < max_repeats(t, 3):
        return _result(3, "repeat", "tests_failing", facts, detail)
    return _result(3, "stop", "tests_failing_after_repeats", facts, detail)


@logged("gate", "gate4_commercial")
def gate4(*, violations: list[str], price: float, price_range: tuple[float, float] | None,
          price_justified: bool, t: dict) -> GateResult:
    detail = {"violations": violations, "price": price, "sourced_range": price_range}
    facts = {"violations": violations, "price": price, "range": price_range}
    if violations:
        return _result(4, "repeat", "wording_violations", facts, detail)
    if price_range and not (price_range[0] <= price <= price_range[1]) and not price_justified:
        return _result(4, "repeat", "price_outside_range", facts, detail)
    return _result(4, "go", "clean_price_sourced" if price_range else "clean_price_assumed", facts, detail)


@logged("gate", "gate5_launch")
def gate5(*, items: list[dict], delivery_tests_passed: bool, withdrawn: bool, t: dict) -> GateResult:
    approved = [i for i in items if i["status"] == "published"]
    rejected = [i for i in items if i["status"] == "rejected"]
    pending = [i for i in items if i["status"] not in {"published", "rejected"}]
    detail = {"published": len(approved), "rejected": len(rejected), "pending": len(pending),
              "delivery_tests_passed": delivery_tests_passed}
    if withdrawn:
        return _result(5, "stop", "launch_withdrawn", {}, detail)
    if pending or rejected or not delivery_tests_passed:
        return _result(5, "repeat", "launch_not_ready", dict(detail), detail)
    return _result(5, "go", "launch_ready", dict(detail), detail, mocked=True)


@logged("gate", "gate6_operate")
def gate6(*, metrics: dict, locked: LockedTargets, memo_passes: bool, alternative_supported: bool, t: dict,
          mocked: bool = True) -> GateResult:
    targets = locked.verify("Gate 6 targets")
    misses = []
    if metrics.get("conversion", 0) < targets["min_conversion"]:
        misses.append({"kind": "conversion", "have": metrics.get("conversion", 0), "need": targets["min_conversion"]})
    if metrics.get("refund_rate", 0) > targets["max_refund_rate"]:
        misses.append({"kind": "refund_rate", "have": metrics.get("refund_rate", 0), "limit": targets["max_refund_rate"]})
    miss_text = [labels.metric_miss(m) for m in misses]
    detail = {"metrics": metrics, "targets": targets, "misses": miss_text}
    facts = {"misses": misses, "mocked": mocked}
    if not memo_passes:
        return _result(6, "repeat", "memo_fails_check", facts, detail, mocked)
    if not misses:
        return _result(6, "go", "targets_met", facts, detail, mocked)
    if alternative_supported:
        return _result(6, "pivot", "targets_missed_alternative", facts, detail, mocked)
    return _result(6, "stop", "targets_missed", facts, detail, mocked)
