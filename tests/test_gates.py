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
    res = gates.gate2(_review(), locked=gates.LockedTargets.lock(locked), result=12, mocked=True,
                      repeats=0, fatal_answered=True, alternative_supported=False, t=T)
    assert res.outcome == "stop" and res.mocked_evidence
    assert res.label == "stop (MOCKED evidence)"


def test_gate2_rejects_result_when_threshold_changed_after_locking():
    locked = {"kind": "waitlist", "metric": "sign-ups", "target": 100, "window_days": 14}
    lk = gates.LockedTargets.lock(locked)
    lk.targets["target"] = 10
    with pytest.raises(gates.ThresholdTamperError):
        gates.gate2(_review(), locked=lk, result=12, mocked=False, repeats=0,
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


def test_fact_checker_replaces_overstated_explanations(make_runtime, models):
    models.fact_check = {"ok": False, "problems": ["'posts from people in Cairo' overstates general articles"],
                         "revised": "The team found general articles about this problem."}
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    report = (rt.ctx.store.outputs / p["memo_file"]).read_text()
    assert "The team found general articles about this problem." in report
    walk = [t["text"] for t in rt.ctx.store.conversation() if t.get("kind") == "gate"][-1]
    assert "The team found general articles about this problem." in walk
    assert "fact_checker" in models.calls


def test_scout_generates_many_versions_and_keeps_a_varied_shortlist(make_runtime, models):
    forms = ["guide", "guide", "guide", "template_pack", "web_tool", "guide"]
    models.variants = [{"id": f"v{i}", "name": f"Idea {i}", "target_customer": "freelance designers",
                        "problem": "clients pay invoices late", "product_form": forms[i - 1], "skills_needed": ["writing"],
                        "est_build_hours": 10, "budget_needed_usd": 0} for i in range(1, 7)]
    rt = make_runtime()
    drive(rt, stop_at_gate=1)
    d = rt.dossier
    assert len(d.shortlist["considered"]) == 6 and len(d.variants) == 4
    assert [v.id for v in d.variants] == ["v1", "v2", "v3", "v4"]
    assert sum(1 for v in d.variants if v.product_form == "guide") <= 2          # variety in the shortlist
    log = rt.ctx.store.log.path.read_text()
    assert '"shortlist_versions"' in log


def test_shortlist_ranks_and_keeps_variety():
    from venture_builder.departments.discover import shortlist
    rows = [{"id": "a", "form": "guide", "rank": .9}, {"id": "b", "form": "guide", "rank": .8},
            {"id": "c", "form": "guide", "rank": .7}, {"id": "d", "form": "web_tool", "rank": .2}]
    assert shortlist(rows, 3) == ["a", "b", "d"]


def test_fact_check_without_a_stated_problem_keeps_the_original(make_runtime, models):
    models.fact_check = {"ok": False, "problems": [], "revised": "Something else entirely."}
    rt = make_runtime()
    p = drive(rt, stop_at_gate=1)
    assert "Something else entirely." not in (rt.ctx.store.outputs / p["memo_file"]).read_text()


# ---- locked targets, per-gate repeat limits, reason codes, thresholds snapshot ----

def test_locking_copies_the_targets_so_later_edits_are_caught():
    source = {"min_conversion": 0.02, "max_refund_rate": 0.1}
    lk = gates.LockedTargets.lock(source)
    source["min_conversion"] = 0.0                       # editing the original doesn't touch the lock
    assert lk.verify()["min_conversion"] == 0.02


def test_gate6_rejects_targets_changed_after_locking():
    lk = gates.LockedTargets.lock({"min_conversion": 0.02, "max_refund_rate": 0.1})
    lk.targets["min_conversion"] = 0.0
    with pytest.raises(gates.ThresholdTamperError):
        gates.gate6(metrics={"conversion": 0.01}, locked=lk, memo_passes=True, alternative_supported=False, t=T)


@pytest.mark.parametrize("gate", [1, 2, 3])
def test_repeat_limits_come_from_config(gate):
    import copy
    locked = gates.LockedTargets.lock({"kind": "waitlist", "metric": "sign-ups", "target": 100, "window_days": 14})

    def run(t, repeats):
        if gate == 1:
            return gates.gate1([], excluded=None, search_failed=False, repeats=repeats, t=t).outcome
        if gate == 2:
            return gates.gate2(_review(), locked=locked, result=60, mocked=False, repeats=repeats, fatal_answered=True,
                               alternative_supported=False, t=t).outcome
        return gates.gate3(tests_passed=1, tests_total=5, loops_used=3, repeats=repeats, promise_unmeetable=False,
                           t=t).outcome

    limit = T[f"gate{gate}"]["max_repeats"]
    assert run(T, limit - 1) == "repeat" and run(T, limit) == "stop"
    t = copy.deepcopy(T)
    t[f"gate{gate}"]["max_repeats"] = limit + 1
    assert run(t, limit) == "repeat" and run(t, limit + 1) == "stop"
    del t[f"gate{gate}"]["max_repeats"]                          # falls back to [limits]
    t["limits"]["max_repeats_per_phase"] = 0
    assert run(t, 0) == "stop"


def test_gates_return_reason_codes_and_labels_word_them():
    from venture_builder import labels
    res = gates.gate4(violations=[], price=50, price_range=(10, 20), price_justified=False, t=T)
    assert res.outcome == "repeat" and res.codes == ["price_outside_range"]
    assert res.reasons == [labels.gate_reason("price_outside_range", {"price": 50, "range": (10, 20)})]
    assert "$50" in res.reasons[0]
    # Every code a gate can return has wording.
    import inspect, re
    from venture_builder.departments import common
    codes = set(re.findall(r'"([a-z_]+)"', inspect.getsource(gates) + inspect.getsource(common)))
    returned = {c for c in codes if c in labels.GATE_REASONS}
    assert len(returned) == len(labels.GATE_REASONS)


def test_run_keeps_the_thresholds_it_started_with(make_runtime, settings, monkeypatch):
    rt = make_runtime()
    start = rt.ctx.t["gate1"]["min_score"]
    assert rt.dossier.thresholds_hash and rt.dossier.thresholds["gate1"]["min_score"] == start
    changed = settings.thresholds()
    changed["gate1"]["min_score"] = 0.99
    monkeypatch.setattr(type(settings), "thresholds", lambda self: changed)
    assert rt.ctx.t["gate1"]["min_score"] == start              # the config file changing mid-run has no effect
    rt.dossier.thresholds["gate1"]["min_score"] = 0.1          # editing the snapshot is refused
    with pytest.raises(gates.ThresholdTamperError):
        rt.ctx.t
    log = [e for e in rt.ctx.store.log.entries if e["action"] == "lock_thresholds"]
    assert len(log) == 1 and log[0]["hash"] == rt.dossier.thresholds_hash


def test_each_run_has_its_own_graph_and_context(make_runtime):
    from venture_builder import orchestrator
    assert not hasattr(orchestrator, "CONTEXTS")
    a, b = make_runtime(), make_runtime()
    a.advance(); b.advance()
    assert a.ctx is not b.ctx and a.graph is not b.graph
    assert a.dossier.history and b.dossier.history
    with pytest.raises(RuntimeError):
        a.graph.invoke({"run_id": b.run_id, "next": "p1_parse", "pending": None, "answer": None},
                       {"configurable": {"thread_id": "other"}})


# ---- scores near the threshold are never shown on the wrong side of it ----

def test_score_just_below_the_bar_is_not_shown_as_meeting_it():
    """Regression: 0.597 was shown as "a score of 60 out of 100 (needs 60)" and treated as a shortfall."""
    from venture_builder import labels
    from venture_builder.schemas import VariantScore
    assert labels.points(0.597, 0.6) == "59.7"
    assert labels.points(0.59996, 0.6) == "59.9"            # one decimal would still say 60: round down instead
    assert labels.points(0.6, 0.6) == "60" and labels.points(0.7) == "70" and labels.points(0.724) == "72.4"
    s = VariantScore(variant_id="v1", pain=0, founder_fit=0, reachability=0, buildability=0, competition_gap=0,
                     score=0.597, pain_signals=1, pain_domains=1, fit_rules_pass=True)
    reason = gates.gate1([s], excluded=None, search_failed=False, repeats=0, t=T, names={"v1": "Kit"}).reasons[0]
    assert "a score of 59.7 out of 100 (needs 60)" in reason and "60 out of 100 (needs 60)" not in reason


def test_breakdown_total_and_blocker_agree_with_the_gate():
    from venture_builder.explain import explain_version
    from venture_builder.schemas import Variant, VariantScore
    v = Variant(id="v1", name="Kit", target_customer="c", problem="p", product_form="guide", est_build_hours=5,
                budget_needed_usd=0)
    s = VariantScore(variant_id="v1", pain=0.2, founder_fit=1, reachability=1, buildability=1, competition_gap=0.5,
                     score=0.597, pain_signals=1, pain_domains=1, fit_rules_pass=True)
    w = {"pain": .3, "founder_fit": .25, "reachability": .2, "buildability": .15, "competition_gap": .1}
    e = explain_version(v, s, w, {"min_pain_signals": 3, "min_source_domains": 2, "min_score": 0.6}, {})
    assert e["total"] == 59.7
    assert any("scores 59.7 out of 100; 60 is needed" in b for b in e["blockers"])


# ---- a memo never claims demand from practice (MOCKED) numbers ----

@pytest.mark.parametrize("mocked", [True, False])
def test_gate_memo_never_claims_demand_from_mocked_numbers(make_runtime, models, mocked):
    from venture_builder.agents import gate_reporter
    rt = make_runtime()
    models.gate_reporter = lambda p: {
        "summary": "Go ahead – you hit the target of 50 sign-ups, showing demand.",
        "explanation": "The waitlist got 50 sign-ups. This proves people want the kit. The numbers don’t prove real "
                       "demand yet. Next, test pricing."}
    with rt.ctx.bound():
        memo = gate_reporter.run(rt.ctx, 2, {"gate": 2, "outcome": "go", "mocked_evidence": mocked}, {})
        stop = gate_reporter.run(rt.ctx, 2, {"gate": 2, "outcome": "stop", "mocked_evidence": mocked}, {})
    text = memo.summary + " " + memo.explanation
    if mocked:
        assert "showing demand" not in text and "proves people want" not in text
        assert "practice numbers (MOCKED)" in memo.summary
        assert memo.summary.startswith("Go ahead. ") and stop.summary.startswith("Stop here. ")   # recommendation kept
        assert "don’t prove real demand yet" in memo.explanation            # negated statements are kept
        assert "Next, test pricing." in memo.explanation
        assert any(e["action"] == "removed_mocked_demand_claim" for e in rt.ctx.store.log.entries)
    else:
        assert "showing demand" in memo.summary                            # real numbers: left as written


# ---- Gate 2 never compares a count with a rate ----

def _conversion_lock(target=0.08):
    return gates.LockedTargets.lock({"kind": "conversion", "metric": "premium purchase rate", "target": target,
                                     "window_days": 30})


def test_gate2_refuses_a_count_against_a_rate_target():
    """Regression: a count of 12 against an 8% target gave GO ("12, which is 15000% of the target of 0.08")."""
    res = gates.gate2(_review(), locked=_conversion_lock(), result=12, mocked=True, repeats=0, fatal_answered=True,
                      alternative_supported=False, t=T)
    assert res.outcome != "go" and res.codes == ["result_unit_mismatch"]
    assert "15000%" not in res.reasons[0] and "share of visitors" in res.reasons[0]


def test_gate2_compares_rates_as_rates():
    low = gates.gate2(_review(), locked=_conversion_lock(), result=0.012, mocked=True, repeats=0, fatal_answered=True,
                      alternative_supported=False, t=T)
    assert low.outcome == "stop" and low.codes == ["far_below_target"]
    assert "1.2%" in low.reasons[0] and "8%" in low.reasons[0]
    met = gates.gate2(_review(), locked=_conversion_lock(), result=0.09, mocked=True, repeats=0, fatal_answered=True,
                      alternative_supported=False, t=T)
    assert met.outcome == "go" and "9%" in met.reasons[0]


def test_stage2_form_asks_for_visitors_and_signups_when_the_target_is_a_rate(make_runtime):
    from conftest import drive
    rt = make_runtime()
    drive(rt, stop_at_gate=1)
    rt.dossier.locked_targets = {"kind": "conversion", "metric": "premium purchase rate", "target": 0.08,
                                 "window_days": 30}
    from venture_builder.departments.validate import p2_signups
    with rt.ctx.bound():
        ask = p2_signups(rt.ctx, None)
    names = [f["name"] for f in ask.payload["fields"]]
    assert "visitors" in names and "signups" in names and "result" not in names
