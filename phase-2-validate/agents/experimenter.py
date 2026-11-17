"""Experimenter agent: designs the landing-page demand test, with thresholds fixed before any data."""

from __future__ import annotations

from .base import as_json, format_brief, load_prompt
from .strategist import label_assumptions

MIN_DURATION, MAX_DURATION = 1, 30


class Experimenter:
    name = "experimenter"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("experimenter")

    def design(self, brief: dict, canvas: dict, feedback: str | None = None):
        user = f"## Founder brief\n{format_brief(brief)}\n\n## Lean Canvas\n{as_json(canvas)}\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"
        user += "\nDesign the demand test. Reply with the JSON object."
        result = self.llm.chat_json(self.system, user, required_keys=("target_signups", "stop_below_signups"))
        return normalise(result.data), result


def _int(v, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def validate_plan(plan: dict) -> list[str]:
    """Problems that make a plan unusable. An empty list means it is valid."""
    problems = []
    if not MIN_DURATION <= plan["duration_days"] <= MAX_DURATION:
        problems.append(f"duration_days must be {MIN_DURATION}-{MAX_DURATION}")
    if plan["min_visitors"] < 1:
        problems.append("min_visitors must be at least 1")
    if plan["target_signups"] < 1:
        problems.append("target_signups must be at least 1")
    if not 0 <= plan["stop_below_signups"] < plan["target_signups"]:
        problems.append("stop_below_signups must be at least 0 and lower than target_signups")
    return problems


def normalise(data: dict) -> dict:
    target = max(1, _int(data.get("target_signups"), 1))
    stop_below = _int(data.get("stop_below_signups"), target // 3)
    if not 0 <= stop_below < target:
        stop_below = target // 3  # keep a real gap between STOP and GO
    try:
        rate = float(data.get("assumed_conversion_rate"))
    except (TypeError, ValueError):
        rate = None
    channels = [{"where": str(c.get("where") or "").strip(), "how": str(c.get("how") or "").strip(),
                 "expected_visitors": max(0, _int(c.get("expected_visitors"), 0))}
                for c in data.get("channels") or [] if isinstance(c, dict)]
    return {
        "hypothesis": str(data.get("hypothesis") or "").strip(),
        "metric": "waitlist_signups",
        "duration_days": max(MIN_DURATION, min(MAX_DURATION, _int(data.get("duration_days"), 14))),
        "min_visitors": max(1, _int(data.get("min_visitors"), 100)),
        "target_signups": target,
        "stop_below_signups": stop_below,
        "assumed_conversion_rate": rate,
        "channels": channels,
        "rationale": str(data.get("rationale") or "").strip(),
        "assumptions": label_assumptions(data.get("assumptions")),
    }
