"""Durability: a run paused mid-phase-5 resumes from state.json without repeating any outward action."""
import json
from pathlib import Path

import yaml

from core.runner import run_variant

ROOT = Path(__file__).resolve().parent.parent
FULL = yaml.safe_load((ROOT / "demo" / "founder_decisions.yaml").read_text(encoding="utf-8"))


def _outbox_entries(log_path):
    return [e for e in json.loads(Path(log_path).read_text(encoding="utf-8")) if e["agent"] == "outbox"]


def test_pause_then_resume_runs_outward_actions_once(tmp_path):
    decisions = {"idea2": {k: v for k, v in FULL["idea2"].items() if k != "phase5.step2.publish_posts"}}
    dfile = tmp_path / "decisions.yaml"
    dfile.write_text(yaml.safe_dump(decisions), encoding="utf-8")
    run_dir = tmp_path / "run"

    first = run_variant("_template", ROOT / "demo" / "founder_brief_idea2.md", dfile, fake=True, run_dir=run_dir)
    assert first["results"][-1]["status"] == "paused"
    store = [e for e in _outbox_entries(first["log_path"]) if e["action"] == "open_store"]
    assert len(store) == 1                                   # store opened before the pause

    decisions["idea2"]["phase5.step2.publish_posts"] = "approve"   # the founder decides later
    dfile.write_text(yaml.safe_dump(decisions), encoding="utf-8")
    second = run_variant(None, resume=True, run_dir=run_dir)
    assert second["results"][-1]["status"] == "completed"
    assert second["stats"]["cached_steps"] > 10              # earlier steps were not recomputed
    assert second["stats"]["llm_calls"] < first["stats"]["llm_calls"]

    entries = _outbox_entries(second["log_path"])
    actions = [e["action"] for e in entries]
    assert actions.count("open_store") == 1 and actions.count("publish_posts") == 1
    outbox_files = list((run_dir / "outbox").iterdir())
    assert len(outbox_files) == len(entries)                 # one file per approved outward action, no duplicates
    log = json.loads(Path(second["log_path"]).read_text(encoding="utf-8"))
    assert [e["action"] for e in log].count("execute_refund") == 1


def test_resume_of_finished_run_does_nothing(tmp_path):
    run_dir = tmp_path / "run"
    run_variant("_template", ROOT / "demo" / "founder_brief_idea2.md", ROOT / "demo" / "founder_decisions.yaml",
                fake=True, run_dir=run_dir)
    again = run_variant(None, resume=True, run_dir=run_dir)
    assert again["stats"]["llm_calls"] == 0 and again["results"][-1]["status"] == "completed"
