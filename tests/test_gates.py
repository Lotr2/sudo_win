"""Gates: STOP for excluded products, repeat on tool failure, STOP on a missed locked target, tamper check,
and evidence that must count for nothing."""

import pytest

from conftest import confirm_gate, drive
from fakes import FakeSearch
from venture_builder import gates
from venture_builder.config import load_settings
from venture_builder.ledger import EvidenceLedger
from venture_builder.schemas import InvestorReview, Risk, Variant
from venture_builder.scoring import score_variant

T = load_settings({}, dotenv=None).thresholds()
SCORING = load_settings({}, dotenv=None).scoring()


def test_gate1_stops_an_excluded_product(make_runtime):
    rt = make_runtime(brief="I want to build a crypto trading bot and sell signals to retail investors.")
    p = drive(rt, stop_at_gate=1)
    assert p["gate"] == 1 and p["recommended"] == "stop"
    assert "Excluded" in p["reasons"][0]
    assert "opportunity_scout" not in rt.ctx.router.clients["oss"].responder.calls


def test_gate1_repeat_when_search_fails(make_runtime):
    rt = make_runtime(search=FakeSearch(fail=True))
    p = drive(rt, stop_at_gate=1)
    assert p["recommended"] == "repeat"
    assert "not evidence of absence" in p["reasons"][0]


def test_gate1_tool_failure_is_never_stop_even_after_repeats():
    res = gates.gate1([], excluded=None, search_failed=True, repeats=5, t=T)
    assert res.outcome == "repeat"


def _review(fatal=False):
    return InvestorReview(scores=[], risks=[Risk(assumption="x", severity=3, fatal=fatal)], objections=[])


def test_gate2_stop_when_12_against_locked_100():
    locked = {"kind": "waitlist", "metric": "sign-ups", "target": 100, "window_days": 14}
    res = gates.gate2(_review(), locked=locked, recorded_hash=gates.lock_hash(locked), result=12, mocked=True,
                      repeats=0, fatal_answered=True, alternative_supported=False, t=T)
    assert res.outcome == "stop" and res.mocked_evidence
    assert res.label == "stop (MOCKED evidence)"


def test_gate2_rejects_result_when_threshold_changed_after_locking():
    locked = {"kind": "waitlist", "metric": "sign-ups", "target": 100, "window_days": 14}
    h = gates.lock_hash(locked)
    locked["target"] = 10
    with pytest.raises(gates.ThresholdTamperError):
        gates.gate2(_review(), locked=locked, recorded_hash=h, result=12, mocked=False, repeats=0,
                    fatal_answered=True, alternative_supported=False, t=T)


def _variant():
    return Variant(id="v1", name="n", target_customer="c", problem="p", product_form="guide", skills_needed=[],
                   est_build_hours=5, budget_needed_usd=0)


@pytest.mark.parametrize("etype", ["simulated", "founder_reported", "assumption", "mocked"])
def test_non_web_evidence_counts_for_nothing_at_gate1(etype):
    ledger = EvidenceLedger()
    ids = [ledger.add(tool="t", type=etype, snippet=f"people hate invoices {i}", url=f"https://s{i}.org/x").id
           for i in range(5)]
    findings = [{"kind": "pain", "claim": "people hate invoices", "evidence_ids": [i], "label": "SOURCED"} for i in ids]
    s = score_variant(_variant(), findings, {"hours_per_week": 10, "budget_usd": 100}, ledger, SCORING, ["guide"])
    assert s.pain_signals == 0 and s.pain == 0
    res = gates.gate1([s], excluded=None, search_failed=False, repeats=2, t=T)
    assert res.outcome == "stop"


def test_web_evidence_counts():
    ledger = EvidenceLedger()
    ids = [ledger.add(tool="t", type="web", snippet=f"people hate invoices {i}", url=f"https://s{i}.org/x").id
           for i in range(3)]
    findings = [{"kind": "pain", "claim": "x", "evidence_ids": [i], "label": "SOURCED"} for i in ids]
    s = score_variant(_variant(), findings, {"hours_per_week": 10, "budget_usd": 100}, ledger, SCORING, ["guide"])
    assert s.pain_signals == 3 and s.pain_domains == 3


def test_override_is_logged_against_recommendation(make_runtime):
    rt = make_runtime(search=FakeSearch(fail=True))
    p = drive(rt, stop_at_gate=1)
    rt.decide_gate("stop", confirm_gate(p), note="I changed my mind")
    rec = rt.ctx.outbox.gate_decision(1, p["attempt"])
    assert rec["override"] == 1 and rec["recommended"] == "repeat"
    assert rt.dossier.status == "stopped"
