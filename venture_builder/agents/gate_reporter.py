"""Gate Reporter (Gemma 4 31B): turns a Gate 1-3 result into a readable memo; it cannot change the outcome.

When the result rests on practice numbers (MOCKED), code removes any sentence that claims they show demand:
the prompt asks for this, and the check makes sure, because the summary is not fact-checked.
"""

import re

from .. import labels
from ..decision_log import log_action
from ..schemas import GateMemo

# "showing demand", "proves people want it", "validates the market" ... but not "doesn't prove real demand".
DEMAND_CLAIM = re.compile(
    r"\b(show(s|ing|ed|n)?|prov(e|es|ing|ed|en)|confirm(s|ing|ed)?|demonstrat\w*|validat\w*|evidence of|proof of|"
    r"sign of|signals?)\b[^.!?]{0,40}\b(demand|interest|people (want|need)|market (need|fit)|traction|appetite)\b",
    re.I)
NEGATED = re.compile(r"\b(no|not|never|isn'?t|aren'?t|doesn'?t|don'?t|didn'?t|can'?t|cannot|won'?t|without|nor)\b"
                     r"[^.!?]{0,30}$", re.I)
PRACTICE_NOTE = ("These are practice numbers (MOCKED): they say nothing yet about whether people want this, and "
                 "real numbers are needed first.")


def _claims_demand(sentence: str) -> bool:
    s = sentence.replace("\u2019", "'")
    for m in DEMAND_CLAIM.finditer(s):
        if not NEGATED.search(s[:m.start()] + m.group(0)[:12]):
            return True
    return False


def remove_mocked_demand_claims(text: str) -> tuple[str, int]:
    """Replace each sentence that claims demand from practice numbers with a plain statement. Returns the
    new text and how many sentences were replaced."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out, n = [], 0
    for part in parts:
        if _claims_demand(part):
            n += 1
            if PRACTICE_NOTE not in out:
                out.append(PRACTICE_NOTE)
        else:
            out.append(part)
    return " ".join(out), n


def run(ctx, gate: int, result: dict, context: dict) -> GateMemo:
    def check(memo: GateMemo) -> None:
        # Stage 1 always offers real alternatives, whatever the outcome, so the founder sees options.
        if gate == 1 and context.get("founder") and len(memo.other_directions) < 2:
            raise ValueError("other_directions needs 2-5 alternatives that grow out of the founder's original idea")

    memo = ctx.router.call("gate_reporter", {"gate": gate, "result": result, "context": context}, GateMemo,
                           action=f"gate{gate}_memo", validate=check)
    from . import fact_checker
    explanation = fact_checker.ground(ctx, memo.explanation, {"result": result, "context": context},
                                      f"stage{gate}_report")
    update = {"explanation": explanation}
    if result.get("mocked_evidence"):
        for field in ("summary", "explanation"):
            text = update.get(field, getattr(memo, field))
            clean, n = remove_mocked_demand_claims(text)
            if n and field == "summary":
                # The summary is one sentence with the recommendation: keep the recommendation in front.
                rec = labels.outcome_name(result.get("outcome", ""), gate)
                if not clean.lower().startswith(rec.lower()):
                    clean = f"{rec}. {clean}"
            if n:
                update[field] = clean
                log_action("gate_reporter", "removed_mocked_demand_claim", field=field, sentences=n, gate=gate)
    return memo.model_copy(update=update)
