"""Policy reviewer agent: flags contradictions and gaps in the policy drafts. It never writes legal text."""

from __future__ import annotations

from .base import as_json, load_prompt

POLICIES = ("terms", "privacy", "refunds")


class PolicyReviewer:
    name = "policy_reviewer"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("policy_reviewer")

    def review(self, facts: dict, texts: dict[str, str]):
        user = f"## Facts about the product and business\n{as_json(facts)}\n"
        for name in POLICIES:
            user += f"\n## Draft: {name}\n{texts.get(name, '')}\n"
        user += "\nReview the drafts. Reply with the JSON object."
        result = self.llm.chat_json(self.system, user, required_keys=("issues",))
        return normalise(result.data), result


def normalise(data: dict) -> dict:
    issues = []
    for i in data.get("issues") or []:
        if not isinstance(i, dict) or not str(i.get("issue") or "").strip():
            continue
        policy = str(i.get("policy") or "").strip().lower()
        severity = str(i.get("severity") or "").strip().lower()
        issues.append({"policy": policy if policy in POLICIES else "general",
                       "severity": severity if severity in ("fix", "consider") else "consider",
                       "issue": str(i["issue"]).strip(), "suggestion": str(i.get("suggestion") or "").strip()})
    return {"issues": issues, "ask_a_lawyer": [str(x) for x in data.get("ask_a_lawyer") or []],
            "summary": str(data.get("summary") or "").strip()}
