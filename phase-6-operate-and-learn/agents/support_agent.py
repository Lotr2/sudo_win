"""Support agent: drafts a grounded reply. Drafts that break the rules are rewritten; the founder approves each one."""

from __future__ import annotations

import support

from .base import as_json, load_prompt


class DraftRejected(Exception):
    def __init__(self, issues):
        super().__init__("; ".join(issues))
        self.issues = issues


class SupportAgent:
    name = "support_agent"

    def __init__(self, llm, max_attempts: int = 3):
        self.llm = llm
        self.system = load_prompt("support_agent")
        self.max_attempts = max_attempts

    def draft(self, context: dict, message: str, orders: list[dict]):
        user = (f"## Product facts\n{as_json(context['facts'])}\n\n## User guide\n{context['guide']}\n\n"
                f"## Policies\n{context['policies']}\n\n## This customer's orders\n{as_json(orders) if orders else '(none found)'}\n\n"
                f"## Customer message\n{message}\n\nDraft the reply. Reply with the JSON object.")
        results, rejected, note = [], [], ""
        for _ in range(self.max_attempts):
            res = self.llm.chat_json(self.system, user + note, required_keys=("category", "reply"))
            results.append(res)
            d = normalise(res.data)
            issues = support.check_reply(d["reply"])
            if not issues:
                return d, results, rejected
            rejected.append(issues)
            note = "\n\n## Your last draft was rejected\n" + "\n".join(f"- {i}" for i in issues) + "\nRewrite it."
        raise DraftRejected(sorted({i for r in rejected for i in r}))


def normalise(data: dict) -> dict:
    cat = str(data.get("category") or "other").strip().lower().replace(" ", "_")
    return {"category": cat if cat in support.CATEGORIES else "other",
            "summary": str(data.get("summary") or "").strip(), "reply": str(data.get("reply") or "").strip(),
            "internal_note": str(data.get("internal_note") or "").strip(),
            "feature_request": data.get("feature_request") or None, "bug": data.get("bug") or None}
