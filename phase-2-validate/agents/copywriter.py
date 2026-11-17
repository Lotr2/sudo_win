"""Copywriter agent: writes waitlist landing-page copy. Every draft is screened in code before the founder sees it."""

from __future__ import annotations

import json

from common.guardrails import screen_copy

from .base import as_json, load_prompt

TEXT_FIELDS = ("product_name", "headline", "subheadline", "who_its_for", "cta_label")
LIST_FIELDS = ("problem_points", "how_it_works")


class CopyRejected(Exception):
    def __init__(self, issues):
        super().__init__(f"copy kept breaking the rules: {', '.join(issues)}")
        self.issues = issues


class Copywriter:
    name = "copywriter"

    def __init__(self, llm, max_attempts: int = 3):
        self.llm = llm
        self.system = load_prompt("copywriter")
        self.max_attempts = max_attempts

    def write(self, canvas: dict, feedback: str | None = None):
        """Returns (copy, results, rejected_drafts). Raises CopyRejected if every attempt breaks the rules."""
        base = f"## Lean Canvas\n{as_json(canvas)}\n"
        if feedback:
            base += f"\n## Founder feedback to apply\n{feedback}\n"
        results, rejected = [], []
        note = ""
        for _ in range(self.max_attempts):
            result = self.llm.chat_json(self.system, base + note + "\nWrite the copy. Reply with the JSON object.",
                                        required_keys=("headline", "cta_label"))
            results.append(result)
            copy = normalise(result.data)
            issues = screen_copy(copy_text(copy))
            if not issues:
                return copy, results, rejected
            rejected.append(issues)
            note = (f"\n## Your last draft was rejected\nIt contained: {', '.join(issues)}. "
                    "Rewrite it without any of that.\n")
        raise CopyRejected(sorted({i for r in rejected for i in r}))


def copy_text(copy: dict) -> str:
    return json.dumps(copy, ensure_ascii=False)


def normalise(data: dict) -> dict:
    copy = {k: str(data.get(k) or "").strip() for k in TEXT_FIELDS}
    for k in LIST_FIELDS:
        v = data.get(k) or []
        copy[k] = [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()][:4]
    copy["faq"] = [{"q": str(f.get("q") or "").strip(), "a": str(f.get("a") or "").strip()}
                   for f in data.get("faq") or [] if isinstance(f, dict) and f.get("q")][:4]
    copy["product_name"] = copy["product_name"] or "Working title"
    copy["cta_label"] = copy["cta_label"] or "Join the waitlist"
    return copy
