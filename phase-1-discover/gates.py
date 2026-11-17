"""Phase 1 gate. Thresholds are fixed here, before any idea is scored, so the model cannot talk its way past them.

The final outcome is the MOST severe of: the hard rules, the score thresholds, and the model's own verdict.
A model that says "go" on weak scores is overruled; it can never upgrade a failing idea.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pre-set evidence thresholds (scores are 1-5).
STOP_IF_ANY_SCORE_AT_OR_BELOW = 1
STOP_IF_AVERAGE_BELOW = 2.5
GO_REQUIRES_EVERY_SCORE_AT_LEAST = 3
GO_REQUIRES_AVERAGE_AT_LEAST = 3.5

SEVERITY = {"go": 0, "pivot": 1, "stop": 2}


@dataclass
class GateResult:
    outcome: str  # go | pivot | stop
    reasons: list[str] = field(default_factory=list)
    average: float = 0.0
    model_verdict: str = ""
    threshold_verdict: str = ""


def threshold_verdict(scores: dict) -> tuple[str, list[str]]:
    vals = list(scores.values())
    avg = sum(vals) / len(vals) if vals else 0
    weak = [k for k, v in scores.items() if v <= STOP_IF_ANY_SCORE_AT_OR_BELOW]
    if weak:
        return "stop", [f"Very weak score (1/5) on: {', '.join(weak)}."]
    if avg < STOP_IF_AVERAGE_BELOW:
        return "stop", [f"Average score {avg:.2f} is below the stop line of {STOP_IF_AVERAGE_BELOW}."]
    below = [k for k, v in scores.items() if v < GO_REQUIRES_EVERY_SCORE_AT_LEAST]
    reasons = []
    if below:
        reasons.append(f"Below {GO_REQUIRES_EVERY_SCORE_AT_LEAST}/5 on: {', '.join(below)}.")
    if avg < GO_REQUIRES_AVERAGE_AT_LEAST:
        reasons.append(f"Average score {avg:.2f} is below the go line of {GO_REQUIRES_AVERAGE_AT_LEAST}.")
    return ("pivot", reasons) if reasons else ("go", [f"Every score at least {GO_REQUIRES_EVERY_SCORE_AT_LEAST} and average {avg:.2f}."])


def discover_gate(evaluation: dict, category_hits: list[str] | None = None) -> GateResult:
    scores = evaluation["scores"]
    avg = sum(scores.values()) / len(scores)
    reasons: list[str] = []
    candidates = []

    # Hard rules
    if not evaluation.get("is_digital", True):
        candidates.append("stop")
        reasons.append("Hard rule: the product is not digital.")
    excluded = [c for c in [evaluation.get("excluded_category"), *(category_hits or [])] if c]
    if excluded:
        candidates.append("stop")
        reasons.append(f"Hard rule: excluded product category ({', '.join(sorted(set(excluded)))}).")

    # Common idea mistakes (YC Startup School, "How to Get and Evaluate Startup Ideas")
    mistakes = evaluation.get("mistakes") or {}
    if mistakes.get("solution_in_search_of_a_problem"):
        candidates.append("stop")
        reasons.append("Solution in search of a problem: the idea starts from a solution, not a real problem people have.")
    if mistakes.get("tar_pit") and not mistakes.get("tar_pit_addressed"):
        candidates.append("pivot")
        hard = mistakes.get("tar_pit_hard_part")
        reasons.append("Tar pit idea: common and looks easy, but keeps failing for a structural reason"
                       + (f" ({hard})" if hard else "") + ", and there is no plan for that hard part yet.")

    t_verdict, t_reasons = threshold_verdict(scores)
    candidates.append(t_verdict)
    reasons += t_reasons

    m_verdict = evaluation.get("verdict", "stop")
    candidates.append(m_verdict)
    if SEVERITY[m_verdict] > SEVERITY[t_verdict]:
        reasons.append(f"The evaluator recommended {m_verdict.upper()} even though the scores alone allowed {t_verdict.upper()}.")

    outcome = max(candidates, key=SEVERITY.__getitem__)
    return GateResult(outcome, reasons, round(avg, 2), m_verdict, t_verdict)
