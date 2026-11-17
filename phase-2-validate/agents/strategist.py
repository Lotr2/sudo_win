"""Strategist agent: builds a Lean Canvas from the concept that passed Phase 1."""

from __future__ import annotations

from .base import as_json, format_brief, load_prompt

LIST_BOXES = ("problem", "solution", "channels", "key_metrics")
TEXT_BOXES = ("existing_alternatives", "customer_segments", "early_adopters", "unique_value_proposition",
              "high_level_concept", "revenue_streams", "cost_structure", "unfair_advantage")
# Lean Canvas order, for display and editing
BOX_ORDER = ("problem", "existing_alternatives", "customer_segments", "early_adopters", "unique_value_proposition",
             "high_level_concept", "solution", "channels", "revenue_streams", "cost_structure", "key_metrics",
             "unfair_advantage")


class Strategist:
    name = "strategist"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("strategist")

    def build(self, handoff: dict, feedback: str | None = None, previous: dict | None = None):
        user = (f"## Founder brief\n{format_brief(handoff['brief'])}\n\n"
                f"## Concept (confirmed in Phase 1)\n{as_json(handoff['concept'])}\n\n"
                f"## Phase 1 evaluation summary\n{handoff.get('evaluation', {}).get('summary', '')}\n")
        if previous:
            user += f"\n## Your previous canvas\n{as_json(previous)}\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"
        user += "\nWrite the Lean Canvas. Reply with the JSON object."
        result = self.llm.chat_json(self.system, user, required_keys=("problem", "unique_value_proposition"))
        return normalise(result.data), result


def _list(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def label_assumptions(items) -> list[str]:
    out = []
    for a in _list(items):
        out.append(a if a.upper().startswith("ASSUMPTION") else f"ASSUMPTION: {a}")
    return out


def normalise(data: dict) -> dict:
    canvas = {k: _list(data.get(k)) for k in LIST_BOXES}
    canvas.update({k: str(data.get(k) or "").strip() for k in TEXT_BOXES})
    canvas["unfair_advantage"] = canvas["unfair_advantage"] or "None yet"
    risks = [r for r in data.get("riskiest_assumptions") or [] if isinstance(r, dict)]
    canvas["riskiest_assumptions"] = [{k: str(r.get(k) or "").strip() for k in ("assumption", "why_risky", "how_to_test")}
                                      for r in risks][:3]
    canvas["assumptions"] = label_assumptions(data.get("assumptions"))
    return canvas
