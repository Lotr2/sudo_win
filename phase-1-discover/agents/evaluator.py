"""Evaluator agent: scores the confirmed concept and recommends go / pivot / stop."""

from __future__ import annotations

from .base import as_json, format_brief, load_prompt

CRITERIA = ("founder_market_fit", "problem_acuteness", "customer_clarity", "willingness_to_pay",
            "differentiation", "market_size", "scalability", "feasibility")
SIGNALS = ("founder_wants_it", "why_now", "proxy_exists", "long_term_interest", "good_idea_space")
VERDICTS = ("go", "pivot", "stop")


class Evaluator:
    name = "evaluator"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("evaluator")

    def evaluate(self, brief: dict, concept: dict, history: list[dict]) -> tuple[dict, object]:
        answers = "\n".join(f"- {t['question']} -> {t['answer']}" for t in history) or "(none)"
        user = (
            f"## Founder brief\n{format_brief(brief)}\n\n"
            f"## Confirmed concept\n{as_json(concept)}\n\n"
            f"## Founder's answers during the interview\n{answers}\n\n"
            "Evaluate it. Reply with the JSON object."
        )
        result = self.llm.chat_json(self.system, user, required_keys=("scores", "verdict"))
        return normalise(result.data), result


def _score(v) -> int:
    try:
        return max(1, min(5, int(float(v))))
    except (TypeError, ValueError):
        return 1  # a missing score counts as weak, never as strong


def normalise(data: dict) -> dict:
    raw_scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    verdict = str(data.get("verdict", "")).strip().lower()
    assumptions = []
    for a in data.get("assumptions") or []:
        a = str(a).strip()
        if a and not a.upper().startswith("ASSUMPTION"):
            a = f"ASSUMPTION: {a}"
        if a:
            assumptions.append(a)
    mistakes = data.get("mistakes") if isinstance(data.get("mistakes"), dict) else {}
    raw_signals = data.get("signals") if isinstance(data.get("signals"), dict) else {}
    signals = {}
    for s in SIGNALS:
        v = raw_signals.get(s) if isinstance(raw_signals.get(s), dict) else {}
        answer = str(v.get("answer", "unclear")).strip().lower()
        signals[s] = {"answer": answer if answer in ("yes", "no", "unclear") else "unclear",
                      "why": str(v.get("why") or "").strip()}
    return {
        "is_digital": data.get("is_digital") is not False,
        "excluded_category": data.get("excluded_category") or None,
        "mistakes": {
            "solution_in_search_of_a_problem": mistakes.get("solution_in_search_of_a_problem") is True,
            "tar_pit": mistakes.get("tar_pit") is True,
            "tar_pit_hard_part": mistakes.get("tar_pit_hard_part") or None,
            "tar_pit_addressed": mistakes.get("tar_pit_addressed") is True,
        },
        "signals": signals,
        "scores": {c: _score(raw_scores.get(c)) for c in CRITERIA},
        "score_reasons": data.get("score_reasons") if isinstance(data.get("score_reasons"), dict) else {},
        "verdict": verdict if verdict in VERDICTS else "stop",
        "summary": str(data.get("summary") or "").strip(),
        "biggest_risks": [str(r) for r in data.get("biggest_risks") or []],
        "pivot_suggestion": data.get("pivot_suggestion") or None,
        "alternatives": [a for a in data.get("alternatives") or [] if isinstance(a, dict)],
        "assumptions": assumptions,
        "interview_questions": [str(q) for q in data.get("interview_questions") or []],
    }
