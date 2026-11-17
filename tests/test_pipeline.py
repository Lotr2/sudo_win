"""Each phase's pipeline runs end to end on the fake client and writes its outputs with the correct names."""

import json
import re

from conftest import drive

EXPECTED = [
    "phase1_step1_opportunities.md", "phase1_step2_scores.json", "phase1_step3_interview_script.md",
    "phase1_gate_decision.md", "phase2_step1_lean_canvas.md", "phase2_step2_experiment.md",
    "phase2_step3_landing_page.html", "phase2_gate_decision.md", "phase3_step1_spec.md",
    "phase3_step2_test_report.md", "phase3_step3_user_guide.md", "phase3_gate_decision.md",
    "phase4_step1_pricing.md", "phase4_step2_policies.md", "phase4_gate_decision.md",
    "phase5_step1_store_page.md", "phase5_step2_launch_posts.md", "payment_test_log.md",
    "phase5_gate_decision.md", "phase6_step1_metrics.md", "phase6_step2_support_log.md",
    "phase6_step3_pivot_memo.md", "phase6_gate_decision.md",
]
NAME = re.compile(r"^(phase\d+_(step\d+|gate)_[\w-]+\.(md|json|csv|txt|html)|payment_test_log\.md)$")


def test_full_pipeline_all_six_phases(make_runtime):
    rt = make_runtime()
    drive(rt)
    files = sorted(p.name for p in rt.ctx.store.outputs.iterdir())
    for name in EXPECTED:
        assert name in files, name
    assert all(NAME.match(f) for f in files), files
    assert rt.dossier.status == "finished"
    assert "PASS" in (rt.ctx.store.outputs / "payment_test_log.md").read_text()
    for n in "123456":
        assert n in rt.dossier.gates


def test_every_log_entry_has_required_keys(make_runtime):
    rt = make_runtime()
    drive(rt)
    log = json.loads(rt.ctx.store.path("decision_log.json").read_text())
    assert len(log) > 50
    for e in log:
        for k in ("timestamp", "agent", "action", "sources", "model", "cost", "approved_by"):
            assert k in e, (k, e)
        assert isinstance(e["sources"], list) and isinstance(e["cost"], (int, float))
    assert any(e["approved_by"] == "founder" for e in log)
