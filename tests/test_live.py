"""Live checks against the real models (skipped by default: run with `pytest -m live`)."""

import pytest

from venture_builder.config import load_settings
from venture_builder.router import Router, doctor
from venture_builder.schemas import LiaisonMessage

pytestmark = pytest.mark.live


def test_doctor_passes():
    ok, lines = doctor(load_settings())
    assert ok, "\n".join(lines)


@pytest.mark.parametrize("role", ["opportunity_scout", "founder_liaison"])
def test_each_model_answers_with_valid_json(role):
    r = Router.from_settings(load_settings())
    out = r.call(role if role == "founder_liaison" else "founder_liaison",
                 {"task": "phase_kickoff", "phase": "test", "assumptions": []}, LiaisonMessage, action="live_check")
    assert out.message
