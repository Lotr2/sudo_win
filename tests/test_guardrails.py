"""Guardrails: every trick prompt is blocked by the Policy Guard; trick prompts live only in tests/."""

import json
from pathlib import Path

import pytest

from venture_builder.policy_guard import PolicyGuard, lint_policies

ROOT = Path(__file__).resolve().parent.parent
TRICKS = json.loads((Path(__file__).parent / "trick_prompts.json").read_text())


@pytest.mark.parametrize("trick", TRICKS, ids=[t["text"][:30] for t in TRICKS])
def test_trick_prompt_blocked_by_rules(trick):
    res = PolicyGuard(router=None).check(trick["text"], context=trick["context"])
    assert not res.allowed
    assert res.category == trick["category"]


@pytest.mark.parametrize("trick", TRICKS, ids=[t["text"][:30] for t in TRICKS])
def test_trick_prompt_blocked_in_chat(make_runtime, trick):
    rt = make_runtime()
    rt.advance()
    out = rt.reply(trick["text"])
    assert "liaison" in out and "can't" in out["liaison"]
    assert rt.ctx.outbox._db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_classifier_on_the_other_model_catches_what_rules_miss(router):
    from venture_builder.decision_log import DecisionLog
    log = DecisionLog(None)
    log.bind()
    res = PolicyGuard(router).check("An innocent line BLOCKME", context="outbox", author_family="gemma")
    assert not res.allowed and res.rule == "classifier"
    entry = [e for e in log.entries if e["agent"] == "policy_classifier"][-1]
    assert entry["model"] == router.settings.model_oss  # Gemma wrote it, GPT-OSS checks it


def test_honest_copy_passes_rules():
    ok = PolicyGuard(router=None).check("A 12-page guide to sending invoices. $19, refunds within 14 days.",
                                        context="outbox", use_classifier=False)
    assert ok.allowed


def test_trick_prompts_live_only_in_tests_or_evals():
    phrases = [t["text"] for t in TRICKS]
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "runs", "tests", "evals", "node_modules"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".html", ".txt"}:
            continue
        text = path.read_text(errors="ignore").lower()
        for ph in phrases:
            assert ph.lower() not in text, f"trick prompt found in {path}"


def test_compliance_linter_flags_income_promise_and_missing_label():
    v = lint_policies("Terms. licence refund contact", "DRAFT: not legal advice collect use contact",
                      "DRAFT: not legal advice. Request within 14 days. We guarantee you will earn money.", 14)
    assert any("missing the 'DRAFT" in x for x in v)
    assert any("income_promise" in x for x in v)


def test_disclaimers_are_not_mistaken_for_promises():
    from venture_builder.policy_guard import check_rules
    for ok in ["We make no guarantees of results.", "This kit does not guarantee results or income.",
               "You won't make passive income from this."]:
        assert check_rules(ok, "output") is None, ok
    for bad in ["We guarantee results within 30 days!", "Guaranteed results or your money back.",
                "Earn passive income fast."]:
        assert check_rules(bad, "output") is not None, bad


def test_memo_critic_sees_the_targets_and_the_data_label(make_runtime):
    from conftest import drive
    rt = make_runtime()
    drive(rt)
    calls = [c for c in rt.ctx.router.clients["oss"].calls if c["role"] == "critic"]
    assert calls and '"targets"' in calls[-1]["user"] and "MOCKED" in calls[-1]["user"]


# ---- legitimate requests and products must still pass the rules (no over-blocking) ----

LEGIT = [
    ("brief", "invoice templates and a late-payment guide for freelance designers"),
    ("brief", "a budgeting spreadsheet template for families"),
    ("brief", "a Notion template for wedding planners"),
    ("brief", "an online course teaching Excel to small business owners"),
    ("brief", "a Canva template pack for bakeries' social media posts"),
    ("brief", "a checklist for landlords to document a flat's condition at move-in"),
    ("brief", "a resume template pack for nurses"),
    ("brief", "a meal-prep grocery list template for busy parents"),
    ("brief", "a pomodoro timer web tool for students"),
    ("brief", "I can invest 5 hours a week and $200 in a study planner for tutors"),
    ("brief", "printable wall planners sold as PDF downloads"),
    ("chat", "can you make the headline shorter?"),
    ("chat", "add a section that explains our 14-day refund policy"),
    ("chat", "what did the research find about competitors?"),
    ("chat", "we'll ask buyers for honest feedback after they have used it"),
    ("chat", "post it on LinkedIn under my own name"),
    ("outbox", "We never store passwords or card numbers."),
    ("outbox", "This guide makes no promise of income or results."),
]


@pytest.mark.parametrize("ctx,text", LEGIT, ids=[t[:30] for _, t in LEGIT])
def test_legitimate_text_passes_the_rules(ctx, text):
    res = PolicyGuard(router=None).check(text, context=ctx, use_classifier=False)
    assert res.allowed, res.reason


def test_every_excluded_area_has_a_plain_words_name():
    from venture_builder import labels
    from venture_builder.policy_guard import EXCLUDED_PRODUCT_RULES
    assert all(r in labels.POLICY_AREAS for r in EXCLUDED_PRODUCT_RULES)
