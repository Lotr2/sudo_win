"""Live checks against the real models (skipped by default: run with `uv run pytest -m live`)."""

import pytest

from venture_builder.config import load_settings
from venture_builder.decision_log import DecisionLog
from venture_builder.router import Router, doctor
from venture_builder.schemas import LiaisonMessage, PolicyVerdict

pytestmark = pytest.mark.live


def test_doctor_passes():
    ok, lines = doctor(load_settings())
    assert ok, "\n".join(lines)


def test_both_models_answer_with_valid_json():
    s = load_settings()
    r = Router.from_settings(s)
    log = DecisionLog(None)
    log.bind()
    msg = r.call("founder_liaison", {"task": "phase_kickoff", "phase": "live check", "assumptions": []},
                 LiaisonMessage, action="live_check")
    assert msg.message
    verdict = r.call("policy_classifier", {"context": "outbox", "text": "A guide to sending invoices, $19."},
                     PolicyVerdict, action="live_check", author_family="gemma")
    assert verdict.allowed
    models = {e["model"] for e in log.entries}
    assert s.model_oss in models
    if not s.single_model:
        assert s.model_gemma in models
