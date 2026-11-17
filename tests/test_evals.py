"""The eval harness itself runs on the test fakes (the real-model evals: python evals/run_evals.py)."""

import sys
from pathlib import Path


def test_eval_harness_runs_every_case_on_fakes(capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
    import run_evals
    code = run_evals.main(["--fake"])
    out = capsys.readouterr().out
    assert "cases pass" in out and "crashed" not in out
    # Excluded products and fake-review requests are stopped even with the fake models (rules decide these).
    for case in ("weak_meal_coach", "crypto_signals", "physical_mugs", "fake_reviews"):
        assert f"| {case} | stop |" in out
    assert code == 0, out


def test_readability_checker_catches_internal_codes():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
    import run_evals
    assert run_evals.readability_problems("Top variant v1 scores 0.47 (as_2)", "x")
    assert not run_evals.readability_problems("The best version scores 47 out of 100.", "x")


def test_classifier_eval_cases_are_all_missed_by_the_rules():
    """The classifier eval only tests the classifier if the rules don't already catch its cases."""
    import json
    from pathlib import Path
    from venture_builder.policy_guard import check_rules
    cases = json.loads((Path(__file__).resolve().parent.parent / "evals" / "classifier_cases.json").read_text())
    assert sum(c["expect"] == "block" for c in cases) >= 5
    assert [c["id"] for c in cases if check_rules(c["text"], c["context"])] == []
