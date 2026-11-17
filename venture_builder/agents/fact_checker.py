"""Fact Checker (GPT-OSS-20B, low): checks text the founder will read against the facts it was written from,
and rewrites anything the facts don't support. It never changes an outcome: it only removes or corrects
statements (for example "posts from people in Cairo" when the sources are general articles)."""

from ..decision_log import log_action
from ..schemas import FactCheck


def ground(ctx, text: str, facts: dict, what: str) -> str:
    """Return `text`, or the checker's corrected version if some statement isn't supported by `facts`."""
    if not text or not ctx.skill("grounding_check"):
        return text
    try:
        check = ctx.router.call("fact_checker", {"text": text, "facts": facts, "what": what}, FactCheck,
                                action=f"fact_check_{what}")
    except Exception:  # noqa: BLE001 - a failed check never blocks the founder; the original text is kept
        log_action("fact_checker", f"fact_check_{what}_skipped")
        return text
    if check.ok or not check.revised.strip() or not [p for p in check.problems if p.strip()]:
        return text          # a rewrite is only accepted with a stated reason
    log_action("fact_checker", f"corrected_{what}", problems=check.problems[:5])
    return check.revised.strip()
