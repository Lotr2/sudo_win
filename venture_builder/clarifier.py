"""Clarifier: code, not the model, decides which founder questions block a decision.

1. Sensitivity check: re-evaluate a decision with plausible alternative values of an open assumption.
   If the result changes (top variant or gate outcome), the assumption is decision-critical.
2. Contradiction check: a new founder answer that conflicts with a confirmed field triggers a
   "which is right?" question instead of a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .assumptions import AssumptionLedger


@dataclass
class BlockingQuestion:
    field: str
    current: Any
    alternatives: list[Any]
    why: str


def alternatives_for(field: str, value: Any) -> list[Any]:
    """Plausible alternative values used by the sensitivity check."""
    if field == "hours_per_week":
        v = float(value or 10)
        return [round(v * 0.4, 1), round(v * 2.5, 1)]
    if field == "budget_usd":
        v = float(value or 200)
        return [round(v * 0.25), round(v * 4)]
    if field == "skills":
        return [[]]
    return []


def sensitivity(field: str, value: Any, evaluate: Callable[[Any], Any]) -> BlockingQuestion | None:
    """`evaluate(value)` returns the decision for that value (e.g. (top_variant, gate_outcome))."""
    base = evaluate(value)
    changed = [alt for alt in alternatives_for(field, value) if evaluate(alt) != base]
    if not changed:
        return None
    return BlockingQuestion(field=field, current=value, alternatives=changed,
                            why=f"With {field} = {changed[0]} instead of {value}, the decision changes.")


def blocking_questions(ledger: AssumptionLedger, values: dict[str, Any],
                       evaluate_with: Callable[[str, Any], Any], decision: str,
                       limit: int = 3) -> list[BlockingQuestion]:
    """Run the sensitivity check over every open assumption that `decision` depends on.
    Marks critical ones in the ledger; returns at most `limit`, highest impact first (order of fields)."""
    found: list[BlockingQuestion] = []
    for a in ledger.open():
        if decision not in a.depends_on or a.field not in values:
            continue
        q = sensitivity(a.field, values[a.field], lambda v, f=a.field: evaluate_with(f, v))
        ledger.mark_critical(a.field, q is not None)
        if q:
            found.append(q)
    return found[:limit]


def _as_number(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v))
    if not m:
        return None
    n = float(m.group(0).replace(",", ""))
    if re.search(r"\d\s*k\b", str(v).lower()):
        n *= 1000
    return n


def contradicts(old: Any, new: Any) -> bool:
    """True when a new answer conflicts with a confirmed value (numbers differ by more than 25%)."""
    a, b = _as_number(old), _as_number(new)
    if a is not None and b is not None:
        if a == b:
            return False
        return abs(a - b) / max(abs(a), abs(b), 1e-9) > 0.25
    return False


def contradiction_question(field: str, old: Any, new: Any) -> str:
    label = field.replace("_usd", "").replace("_", " ")
    return (f"Earlier you told me your {label} was {old}, and now {new}. "
            f"Which is right? I'll use the one you confirm.")
