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
    assert "which the team doesn't help with" in p["reasons"][0]
    assert "opportunity_scout" not in rt.ctx.router.clients["oss"].responder.calls


def test_gate1_repeat_when_search_fails(make_runtime):
    rt = make_runtime(search=FakeSearch(fail=True))
    p = drive(rt, stop_at_gate=1)
    assert p["recommended"] == "repeat"
    assert "says nothing bad about your idea" in p["reasons"][0]


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


def test_gate_report_is_human_readable(make_runtime):
    import re
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    report = (rt.ctx.store.outputs / p["memo_file"]).read_text()
    assert "```" not in report                                      # no raw JSON or code blocks
    assert not re.search(r"\b(v\d|as_\d+|ev_\d+|log_\d+)\b", report)  # no internal ids
    assert not re.search(r"\b(budget_usd|hours_per_week|pain_signals|fit_rules_pass)\b", report)
    assert not re.search(r"\b(budget_usd|hours_per_week|pain_signals)\b", " ".join(p["reasons"]))


def test_stage1_explains_every_point(make_runtime):
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    assert p["versions"], "the decision card carries the score breakdown"
    for v in p["versions"]:
        assert abs(sum(f["points"] for f in v["factors"]) - v["total"]) <= 2      # points add up (rounding)
        assert all(f["why"] for f in v["factors"])                               # every factor has a reason
        assert bool(v["blockers"]) == (v["verdict"] != "Passes every check.")
    report = (rt.ctx.store.outputs / p["memo_file"]).read_text()
    assert "How each version scored" in report
    docs = (rt.ctx.store.outputs / "phase1_step1_opportunities.md").read_text()
    assert "What the research found" in docs and "Who it's for" in docs


def test_skill_matching_is_fair():
    from venture_builder.scoring import match_skills, skill_coverage
    assert skill_coverage(["web development", "database design"], ["web dev"]) == 1.0
    assert skill_coverage(["content writing", "marketing copy"], ["marketing"]) == 1.0
    covered, learnable, missing = match_skills(["basic design", "tax law"], ["marketing"])
    assert learnable == ["basic design"] and missing == ["tax law"]


def test_breakdown_flags_evidence_that_does_not_mention_the_market():
    from venture_builder.explain import explain_version
    from venture_builder.schemas import Variant, VariantScore
    v = Variant(id="v1", name="Mechanic Guide", target_customer="car owners in Cairo", problem="p",
                product_form="guide", est_build_hours=20, budget_needed_usd=200)
    s = VariantScore(variant_id="v1", pain=0.6, founder_fit=1, reachability=0, buildability=1, competition_gap=0.4,
                     score=0.6, pain_signals=3, pain_domains=3, fit_rules_pass=True,
                     inputs={"problem_claims": ["People struggle to find honest mechanics."], "problem_sites": ["blog.example.com"]})
    w = {"pain": .3, "founder_fit": .25, "reachability": .2, "buildability": .15, "competition_gap": .1}
    g1 = {"min_pain_signals": 3, "min_source_domains": 2, "min_score": 0.6}
    why = explain_version(v, s, w, g1, {"market": "people in Cairo"})["factors"][0]["why"]
    assert "don't mention your market" in why or "none of these examples mention your market" in why
    s.inputs["problem_claims"] = ["Drivers in Cairo say mechanics overcharge."]
    assert "mention your market" not in explain_version(v, s, w, g1, {"market": "people in Cairo"})["factors"][0]["why"]
