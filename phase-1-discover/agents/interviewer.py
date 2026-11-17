"""Interviewer agent: works out what the founder's concept actually is, asking questions until it is clear."""

from __future__ import annotations

from .base import format_brief, format_history, load_prompt

UNDERSTOOD_THRESHOLD = 80
CONCEPT_KEYS = ("one_liner", "problem", "customer", "product", "value", "revenue", "founder_fit",
                "idea_origin", "current_alternatives")


class Interviewer:
    name = "interviewer"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("interviewer")

    def assess(self, brief: dict, history: list[dict]) -> tuple[dict, object]:
        user = (
            f"## Founder brief\n{format_brief(brief)}\n\n"
            f"## Conversation so far\n{format_history(history)}\n\n"
            "Do you understand the concept? Reply with the JSON object."
        )
        result = self.llm.chat_json(self.system, user, required_keys=("understood", "concept", "questions"))
        return normalise(result.data), result


def normalise(data: dict) -> dict:
    """Coerce model output into a predictable shape and apply the threshold in code, not just in the prompt."""
    concept = data.get("concept") if isinstance(data.get("concept"), dict) else {}
    concept = {k: str(concept.get(k) or "").strip() for k in CONCEPT_KEYS}
    try:
        score = int(float(data.get("understanding_score", 0)))
    except (TypeError, ValueError):
        score = 0
    questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()][:3]
    understood = bool(data.get("understood")) and score >= UNDERSTOOD_THRESHOLD and all(concept.values())
    if not understood and not questions:
        empty = [k for k, v in concept.items() if not v]
        questions = [f"Can you say more about the {empty[0].replace('_', ' ')}?" if empty
                     else "What is still unclear or missing from the summary above?"]
    return {
        "understood": understood,
        "understanding_score": max(0, min(100, score)),
        "concept": concept,
        "gaps": [str(g) for g in data.get("gaps") or []],
        "assumptions": [str(a) for a in data.get("assumptions") or []],
        "questions": [] if understood else questions,
    }
