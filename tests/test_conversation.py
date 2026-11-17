"""Founder conversation: read-back blocks progress, the Clarifier picks blocking questions, GO waits for
critical assumptions, corrections re-run only dependent steps, contradictions are asked, "why?" answers
cite the record."""

import pytest

from conftest import confirm_gate, drive
from fakes import FakeSearch, fake_fetch
from venture_builder.agents.founder_liaison import NOT_IN_RECORD
from venture_builder.clarifier import contradicts, sensitivity
from venture_builder.config import load_settings
from venture_builder.llm_clients import FakeClient
from venture_builder.router import Router
from venture_builder.runtime import BlockedDecision, Runtime


def test_nothing_after_intake_runs_until_readback_confirmed(make_runtime, models):
    rt = make_runtime()
    p = rt.advance()
    assert p["kind"] == "readback"
    assert "opportunity_scout" not in models.calls and "market_researcher" not in models.calls
    assert rt.pending()["kind"] == "readback"
    rt.reply("yes")
    assert "opportunity_scout" in models.calls


def test_readback_correction_reads_back_again(make_runtime, models):
    rt = make_runtime()
    rt.advance()
    rt.reply("No, I can only do 3 hours a week")
    assert rt.pending()["kind"] == "readback"
    assert rt.dossier.profile.hours_per_week == 3
    assert "opportunity_scout" not in models.calls


def _demo_runtime(tmp_path, models, **profile):
    s = load_settings({"RUNS_DIR": str(tmp_path / "runs"), "GEMINI_API_KEY": "k", "RUN_MODE": "demo"}, dotenv=None)
    models.profile.update(profile)
    fake = FakeClient(models)
    return Runtime.new(s, "designer brief", router=Router(s, fake, fake), search=FakeSearch(), fetch=fake_fetch)


def test_demo_mode_readback_times_out_and_continues(tmp_path, models):
    rt = _demo_runtime(tmp_path, models)
    p = rt.advance()
    assert p["kind"] == "readback" and p["timeout"] == 30
    rt.timeout()
    assert "opportunity_scout" in models.calls
    assert not rt.dossier.readback_confirmed


def test_sensitivity_blocking_only_when_decision_changes():
    flips = sensitivity("hours_per_week", 5, lambda v: ("v1", "go") if v >= 3 else ("v2", "repeat"))
    assert flips is not None and flips.alternatives == [2.0]
    stays = sensitivity("hours_per_week", 5, lambda v: ("v1", "go"))
    assert stays is None


def _to_gate1_with_unanswered_hours(tmp_path, models):
    rt = _demo_runtime(tmp_path, models, hours_per_week=None, gaps=["hours_per_week"])
    p = rt.advance()
    while p["kind"] != "gate":
        rt.timeout()  # the founder does not answer: hours stays a system ASSUMPTION
        p = rt.pending()
    return rt, p


def test_assumption_that_flips_the_outcome_becomes_blocking(tmp_path, models):
    rt, p = _to_gate1_with_unanswered_hours(tmp_path, models)
    a = rt.ctx.assumptions.by_field("hours_per_week")
    assert a.status == "assumed" and a.critical
    assert any(x.startswith("clarify:hours_per_week") for x in rt.dossier.asked)
    budget = rt.ctx.assumptions.by_field("budget_usd")
    assert not budget.critical  # confirmed in the brief, changes nothing


def test_go_on_unconfirmed_critical_assumption_cannot_be_accepted(tmp_path, models):
    rt, p = _to_gate1_with_unanswered_hours(tmp_path, models)
    assert p["recommended"] == "go" and "hours_per_week" in p["critical_unconfirmed"]
    with pytest.raises(BlockedDecision):
        rt.decide_gate("go", confirm_gate(p), variant_id=p["suggested_variant"])
    rt.confirm_assumption("hours_per_week")
    rt.decide_gate("go", confirm_gate(p), variant_id=p["suggested_variant"])
    assert rt.dossier.chosen_variant


def test_correction_reruns_only_dependent_steps(tmp_path, models):
    rt, p = _to_gate1_with_unanswered_hours(tmp_path, models)
    research_calls = models.calls.count("market_researcher") + models.calls.count("competitor_analyst")
    scout_calls = models.calls.count("opportunity_scout")
    out = rt.confirm_assumption("hours_per_week", "12")
    assert out["rerun"] == "p1_score"
    p2 = rt.pending()
    assert p2["kind"] == "gate" and p2["attempt"] == p["attempt"] + 1
    assert models.calls.count("market_researcher") + models.calls.count("competitor_analyst") == research_calls
    assert models.calls.count("opportunity_scout") == scout_calls
    assert rt.dossier.profile.hours_per_week == 12


def test_contradictory_answers_trigger_which_is_right(make_runtime, models):
    models.profile.update(budget_usd=150, hours_per_week=None, gaps=["hours_per_week"])
    rt = make_runtime()
    p = rt.advance()
    assert p["kind"] == "question" and p["field"] == "hours_per_week"
    rt.reply("Actually I can spend $1,000 on this")
    p = rt.pending()
    assert p["kind"] == "question" and "Which is right?" in p["question"]
    assert "150" in p["question"] and "1000" in p["question"].replace(",", "")
    assert contradicts(150, "$1,000") and not contradicts(150, 160)


def test_why_answers_cite_only_the_record(make_runtime):
    rt = make_runtime()
    drive(rt, stop_at_gate=1)
    record = rt.record_index()
    ans = rt.ask("why did gate 1 recommend this outcome?")
    cited = ans[ans.rfind("[") + 1:-1].split(", ")
    assert all(c in record for c in cited)
    assert rt.ask("qqqq zzzz xxxx") == NOT_IN_RECORD


def test_why_answer_with_invented_citation_says_not_in_record(make_runtime, models):
    rt = make_runtime()
    rt.advance()
    models.founder_liaison = lambda p: {"answer": "Because of ev_404.", "cited_ids": ["ev_404"]} \
        if p.get("task") == "ask_anything" else type(models).founder_liaison(models, p)
    assert rt.ask("why?") == NOT_IN_RECORD


def test_founder_reported_answers_never_count_as_market_evidence(make_runtime):
    rt = make_runtime()
    drive(rt, stop_at_gate=1)
    notes = rt.ctx.ledger.add(tool="founder_notes", type="founder_reported", snippet="I know designers will pay")
    assert rt.ctx.ledger.gate_weight(notes.id) == 0
    assert rt.ctx.ledger.check_claim("Designers will pay", [notes.id]).label == "ASSUMPTION"


def test_conversation_log_is_written(make_runtime):
    rt = make_runtime()
    drive(rt, stop_at_gate=1)
    turns = rt.ctx.store.conversation()
    assert turns[0]["speaker"] == "founder" and any(t["speaker"] == "liaison" for t in turns)
    assert all({"id", "timestamp", "speaker", "phase", "text"} <= t.keys() for t in turns)
