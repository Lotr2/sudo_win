"""Trick prompts are refused; honest briefs and copy pass."""
from pathlib import Path

import pytest

import guardrails
from core.brief import load_brief

ROOT = Path(__file__).resolve().parent.parent
RULE_CASES = sorted(p for p in (ROOT / "evals" / "trick_prompts").glob("*.md")
                    if p.name != "README.md" and not p.name.endswith(".verifier.md"))


@pytest.mark.parametrize("path", RULE_CASES, ids=lambda p: p.stem)
def test_trick_prompt_refused_by_rules(path):
    assert not guardrails.screen_request(load_brief(path).text).allowed


@pytest.mark.parametrize("name", ["founder_brief_idea1.md", "founder_brief_idea2.md"])
def test_demo_briefs_allowed(name):
    res = guardrails.screen_request((ROOT / "demo" / name).read_text(encoding="utf-8"))
    assert res.allowed, res.reasons


@pytest.mark.parametrize("text", ["Earn $3,000 a month guaranteed!", "Only 3 copies left, offer ends tonight",
                                  "★★★★★ Join 10,000 happy customers", "## Testimonials\nGreat product"])
def test_lint_catches_dishonest_copy(text):
    assert guardrails.lint(text)


def test_lint_allows_honest_copy_and_negations():
    assert not guardrails.lint("Editable lesson templates. No income is guaranteed. No testimonials, no countdowns.")
