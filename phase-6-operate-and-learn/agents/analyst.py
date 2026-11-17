"""Analyst agent: writes the persevere-or-pivot memo around a decision code has already made."""

from __future__ import annotations

from .base import as_json, load_prompt

VERDICTS = ("confirmed", "refuted", "untested")


class Analyst:
    name = "analyst"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("analyst")

    def memo(self, outcome: str, reasons: list[str], metrics: dict, targets: dict, support_summary: dict,
             concept: dict, risky: list):
        user = (f"## outcome (fixed)\n{outcome}\n\n## reasons\n" + "\n".join(f"- {r}" for r in reasons) +
                f"\n\n## Metrics (computed by code)\n{as_json(metrics)}\n\n## Targets (set before launch)\n{as_json(targets)}\n\n"
                f"## Support summary\n{as_json(support_summary)}\n\n## Original concept\n{as_json(concept)}\n\n"
                f"## Riskiest assumptions (Lean Canvas)\n{as_json(risky)}\n\nWrite the memo. Reply with the JSON object.")
        result = self.llm.chat_json(self.system, user, required_keys=("headline", "what_happened"))
        return normalise(result.data), result


def _list(v) -> list[str]:
    return [str(x).strip() for x in (v or []) if str(x).strip()] if isinstance(v, list) else []


def normalise(d: dict) -> dict:
    exp = d.get("next_experiment") if isinstance(d.get("next_experiment"), dict) else {}
    return {
        "headline": str(d.get("headline") or "").strip(), "what_happened": str(d.get("what_happened") or "").strip(),
        "assumptions": [{"assumption": str(a.get("assumption") or ""),
                         "verdict": a.get("verdict") if a.get("verdict") in VERDICTS else "untested",
                         "evidence": str(a.get("evidence") or "")}
                        for a in d.get("assumptions") or [] if isinstance(a, dict)],
        "learnings": _list(d.get("learnings")), "support_themes": _list(d.get("support_themes")),
        "next_steps": _list(d.get("next_steps")),
        "next_experiment": {k: str(exp.get(k) or "") for k in ("hypothesis", "metric", "target")},
        "pivot_options": [{k: str(p.get(k) or "") for k in ("change", "option", "evidence")}
                          for p in d.get("pivot_options") or [] if isinstance(p, dict)],
        "wind_down": _list(d.get("wind_down")),
    }
