"""Several ideas at once on both endpoints; truncation, invalid JSON and a dead endpoint are handled without loops."""
import json
from pathlib import Path

from core.runner import run_variant

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
IDEA2 = ROOT / "demo" / "founder_brief_idea2.md"
DECISIONS = ROOT / "demo" / "founder_decisions.yaml"


def test_two_ideas_in_parallel(tmp_path):
    s = run_variant("_template", [IDEA2, FIX / "brief_idea3.md"], FIX / "decisions_two_ideas.yaml", fake=True,
                    run_dir=tmp_path / "run")
    assert sorted((r["idea"], r["status"]) for r in s["results"]) == [("idea2", "completed"), ("idea3", "completed")]
    names = {p.name for p in Path(s["outputs_dir"]).iterdir()}
    assert "phase6_gate_idea2-repeat.md" in names and "phase6_gate_idea3-repeat.md" in names
    log = (Path(s["outputs_dir"]) / "payment_test_log.md").read_text(encoding="utf-8")
    assert "## idea2" in log and "## idea3" in log
    json.loads(Path(s["log_path"]).read_text(encoding="utf-8"))           # log stays valid JSON under concurrency
    assert all(v["served"] > 0 for v in s["endpoints"].values())          # both GPUs were used


def test_dead_endpoint_fails_over(tmp_path):
    s = run_variant("_template", IDEA2, DECISIONS, fake=True, faults={"down": ["fake-b"]}, run_dir=tmp_path / "run")
    assert s["results"][-1]["status"] == "completed"
    assert s["endpoints"]["fake-b"]["served"] >= 1 and s["endpoints"]["fake-b"]["failures"] >= 1


def test_invalid_json_is_retried_with_the_error(tmp_path):
    s = run_variant("_template", IDEA2, DECISIONS, fake=True, faults={"invalid_json": 1}, run_dir=tmp_path / "run")
    assert s["stats"]["json_retries"] == 1 and s["results"][-1]["status"] == "completed"


def test_truncated_step_fails_once_and_the_run_continues(tmp_path):
    s = run_variant("_template", IDEA2, DECISIONS, fake=True, faults={"length_if": "TASK (Lean Canvas)"},
                    run_dir=tmp_path / "run")
    assert "idea2:phase2_step1" in s["stats"]["failed_steps"] and s["stats"]["truncations"] == 1
    text = (Path(s["outputs_dir"]) / "phase2_step1_idea2-lean-canvas.md").read_text(encoding="utf-8")
    assert "STEP FAILED" in text and "finish_reason=length" in text
    assert s["results"][-1]["status"] in ("completed", "stopped")      # gates decide; nothing loops


def test_truncation_fails_the_step_and_verifier_fails_closed(tmp_path):
    # The first call is the verifier's request screen: truncated -> verifier error -> request refused (fail closed).
    s = run_variant("_template", IDEA2, DECISIONS, fake=True, faults={"length": 1}, run_dir=tmp_path / "run")
    assert s["stats"]["truncations"] == 1
    assert s["results"][-1]["status"] == "refused"
    assert s["stats"]["llm_calls"] == 1                                    # no retry loop on truncation
