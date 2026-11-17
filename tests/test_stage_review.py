"""Stage review: revise / redo / reset, per-attempt founder decisions, retry cap, history, superseded outbox items."""
import json
from pathlib import Path

import yaml

import gates
from core.runner import run_variant

ROOT = Path(__file__).resolve().parent.parent
IDEA2 = ROOT / "demo" / "founder_brief_idea2.md"
BASE = yaml.safe_load((ROOT / "demo" / "founder_decisions.yaml").read_text(encoding="utf-8"))["idea2"]


def _run(tmp_path, extra: dict, faults: dict | None = None, variant: str = "anan"):
    dfile = tmp_path / "decisions.yaml"
    dfile.write_text(yaml.safe_dump({"idea2": {**BASE, **extra}}), encoding="utf-8")
    s = run_variant(variant, IDEA2, dfile, fake=True, faults=faults, run_dir=tmp_path / "run")
    log = json.loads(Path(s["log_path"]).read_text(encoding="utf-8"))
    return s, log


def _count(log, action):
    return sum(1 for e in log if e["action"] == action)


def test_revise_reruns_only_failed_steps_and_passes_on_recheck(tmp_path):
    bodies = []
    s, log = _run(tmp_path, {"phase3.gate@2": "accept"}, faults={"docs_drop": 1, "record": bodies})
    assert s["results"][-1]["status"] == "completed"
    # gate 3 attempt 1 failed (docs missed a feature) -> founder accepted -> revise; only docs + tests re-ran
    assert _count(log, "phase3_step1:mvp-spec") == 1 and _count(log, "phase3_step2:mvp") == 1
    assert _count(log, "phase3_step3:user-docs") == 2 and _count(log, "phase3_step4:test-report") == 2
    revision = [b for b in bodies if "REVISION (attempt 2)" in b["messages"][-1]["content"]]
    assert len(revision) == 1
    prompt = revision[0]["messages"][-1]["content"]
    assert "feature not in user docs: Feature 2" in prompt and "PREVIOUS ANSWER" in prompt
    assert revision[0]["seed"] == 42 + 1000                     # a new attempt samples differently
    hist = tmp_path / "run" / "attempts" / "idea2" / "phase3"
    a1 = json.loads((hist / "1" / "attempt.json").read_text(encoding="utf-8"))
    a2 = json.loads((hist / "2" / "attempt.json").read_text(encoding="utf-8"))
    assert (a1["passed"], a1["final"]) == (False, "revise") and (a2["passed"], a2["final"]) == (True, "go")
    assert (hist / "1" / "phase3_step3_idea2-user-docs.md").exists()       # earlier attempt kept
    gate_files = sorted(p.name for p in Path(s["outputs_dir"]).glob("phase3_gate_*"))
    assert gate_files == ["phase3_gate_idea2-go.md"]                      # no stale -repeat file in outputs
    assert s["stats"]["phase_retries"] == 1


def test_founder_decision_is_not_replayed_for_the_next_attempt(tmp_path):
    s, log = _run(tmp_path, {}, faults={"docs_drop": 1})
    assert s["results"][-1]["status"] == "paused"                         # attempt 2 waits for a new decision
    assert any(e["action"] == "awaiting:phase3.gate" and e.get("attempt") == 2 for e in log)


def test_retry_cap_hands_the_decision_back_to_the_founder(tmp_path):
    s, log = _run(tmp_path, {"phase3.gate@2": "revise", "phase3.gate@3": {"choice": "redo", "note": "simpler docs"},
                             "phase3.gate@4": "revise"}, faults={"docs_drop": 99})
    assert s["results"][-1]["status"] == "paused" and s["stats"]["phase_retries"] == 3
    invalid = [e for e in log if e["action"] == "invalid:phase3.gate"]
    assert invalid and invalid[0]["attempt"] == 4 and "revise" not in invalid[0]["allowed"]
    assert {"go", "pivot", "stop"} <= set(invalid[0]["allowed"])
    hist = tmp_path / "run" / "attempts" / "idea2" / "phase3"
    assert all((hist / str(n) / "attempt.json").exists() for n in (1, 2, 3))   # decided attempts are kept
    assert not (hist / "4").exists()                                         # attempt 4 still awaits the founder
    a3 = json.loads((hist / "3" / "attempt.json").read_text(encoding="utf-8"))
    assert (a3["final"], a3["note"]) == ("redo", "simpler docs")


def test_redo_with_note_needs_new_approval_and_supersedes_old_outbox_item(tmp_path):
    bodies = []
    s, log = _run(tmp_path, {"phase2.gate": {"choice": "redo", "note": "Focus on time saved per lesson"},
                             "phase2.step2.publish_landing_page@2": "approve", "phase2.gate@2": "accept"},
                  faults={"record": bodies})
    assert s["results"][-1]["status"] == "completed"
    assert any("FOUNDER NOTE: Focus on time saved per lesson" in b["messages"][-1]["content"] for b in bodies)
    outbox = tmp_path / "run" / "outbox"
    old = outbox / "idea2__phase2.step2.publish_landing_page.txt"
    new = outbox / "idea2__phase2.step2.publish_landing_page__a2.txt"
    assert new.exists() and old.read_text(encoding="utf-8").startswith("<!-- SUPERSEDED")
    assert any(e["action"] == "supersede:publish_landing_page" for e in log)
    assert any(e["action"] == "approve:phase2.step2.publish_landing_page" and e.get("attempt") == 2 for e in log)


def test_reset_at_gate_5_uses_new_mock_orders_and_new_approvals(tmp_path):
    s, log = _run(tmp_path, {"phase5.gate": "reset", "phase5.step1.open_store@2": "approve",
                             "phase5.step2.publish_posts@2": "approve", "phase5.step4.refund@2": "approve",
                             "phase5.gate@2": "accept"})
    assert s["results"][-1]["status"] == "completed"
    commerce = json.loads((tmp_path / "run" / "commerce_idea2.json").read_text(encoding="utf-8"))
    assert sorted(commerce["orders"]) == ["idea2-a2-order-001", "idea2-a2-order-002"]
    assert (tmp_path / "run" / "attempts" / "idea2" / "phase5" / "1" / "commerce.json").exists()
    assert _count(log, "execute_refund") == 2                              # one per attempt, each founder-approved
    log_md = (Path(s["outputs_dir"]) / "payment_test_log.md").read_text(encoding="utf-8")
    assert "idea2-a2-order-001" in log_md and "idea2-order-001" not in log_md


def test_gate_choices():
    assert "reset" in gates.choices(5, "go", True) and "revise" not in gates.choices(5, "repeat", True)
    six = gates.choices(6, "repeat", True)
    assert "accept" in six and not {"revise", "redo", "reset"} & set(six)
    capped = gates.choices(3, "repeat", False)
    assert "accept" not in capped and not {"revise", "redo"} & set(capped)
    assert gates.resolve(3, "accept", "repeat") == "revise" and gates.resolve(5, "repeat", "repeat") == "reset"
