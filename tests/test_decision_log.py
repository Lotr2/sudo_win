"""decision_log.json entries always carry the required keys with the right types."""
import json

import pytest

from core.conventions import LOG_KEYS
from core.log import DecisionLog, LogSchemaError


def test_entries_have_all_keys(tmp_path):
    log = DecisionLog(tmp_path / "d.json")
    log.append("researcher", "find_opportunities", sources=["https://example.org/a"], model="qwen3:8b")
    log.append("gate", "phase2_gate", outcome="stop")
    for e in json.loads((tmp_path / "d.json").read_text(encoding="utf-8")):
        assert all(k in e for k in LOG_KEYS)


@pytest.mark.parametrize("kw", [{"sources": ["not-a-url"]}, {"approved_by": "claude"}, {"cost": "free"}])
def test_bad_entries_rejected(tmp_path, kw):
    with pytest.raises(LogSchemaError):
        DecisionLog(tmp_path / "d.json").append("a", "b", **kw)
