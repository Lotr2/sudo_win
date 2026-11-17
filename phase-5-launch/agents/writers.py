"""Store copywriter and launch writer. Every draft is screened in code; a draft that keeps breaking the rules is
never shown to the founder.
"""

from __future__ import annotations

import json
import re

from common.guardrails import screen_copy

from .base import as_json, load_prompt

URL_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|io|net|org|app|co)\b", re.I)
IMPERSONATION_RE = re.compile(r"\b(as a (happy |satisfied )?(customer|user)|i('m| am) not affiliated|"
                              r"a friend (built|made)|i found this (tool|app))\b", re.I)


class DraftRejected(Exception):
    def __init__(self, issues):
        super().__init__("; ".join(issues))
        self.issues = issues


def _facts(handoff: dict) -> str:
    spec = handoff.get("spec") or {}
    canvas = handoff.get("canvas") or {}
    return as_json({
        "product": spec.get("product_name"), "summary": spec.get("summary"),
        "features": [s.get("story") for s in spec.get("stories") or []], "out_of_scope": spec.get("out_of_scope"),
        "price": handoff.get("price_line"), "early_adopters": canvas.get("early_adopters"),
        "problem": canvas.get("problem"), "value_proposition": canvas.get("unique_value_proposition"),
        "channels": canvas.get("channels"), "founder_values": (handoff.get("brief") or {}).get("values"),
    })


class _Writer:
    prompt = ""
    required: tuple[str, ...] = ()

    def __init__(self, llm, max_attempts: int = 3):
        self.llm = llm
        self.system = load_prompt(self.prompt)
        self.max_attempts = max_attempts

    def _run(self, user: str, normalise, check):
        results, rejected, note = [], [], ""
        for _ in range(self.max_attempts):
            res = self.llm.chat_json(self.system, user + note, required_keys=self.required)
            results.append(res)
            draft = normalise(res.data)
            issues = check(draft)
            if not issues:
                return draft, results, rejected
            rejected.append(issues)
            note = "\n\n## Your last draft was rejected\n" + "\n".join(f"- {i}" for i in issues) + "\nRewrite it."
        raise DraftRejected(sorted({i for r in rejected for i in r}))


class StoreCopywriter(_Writer):
    name, prompt, required = "store_copywriter", "store_copywriter", ("headline",)

    def write(self, handoff: dict, feedback: str | None = None):
        user = f"## Product facts (approved)\n{_facts(handoff)}\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"

        def normalise(d):
            return {"headline": str(d.get("headline") or "").strip(), "subheadline": str(d.get("subheadline") or "").strip(),
                    "who_its_for": str(d.get("who_its_for") or "").strip(),
                    "faq": [{"q": str(f.get("q") or "").strip(), "a": str(f.get("a") or "").strip()}
                            for f in d.get("faq") or [] if isinstance(f, dict) and f.get("q")][:4]}

        def check(c):
            text = json.dumps(c, ensure_ascii=False)
            issues = [f"copy rule: {i}" for i in screen_copy(text)]
            if URL_RE.search(text):
                issues.append("no links or web addresses")
            if not c["headline"]:
                issues.append("headline is empty")
            return issues

        return self._run(user + "\nWrite the store copy. Reply with the JSON object.", normalise, check)


class LaunchWriter(_Writer):
    name, prompt, required = "launch_writer", "launch_writer", ("posts",)

    def write(self, handoff: dict, feedback: str | None = None):
        user = f"## Product facts (approved)\n{_facts(handoff)}\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"

        def normalise(d):
            return [{"channel": str(p.get("channel") or "").strip(), "title": str(p.get("title") or "").strip(),
                     "body": str(p.get("body") or "").strip(), "etiquette": str(p.get("etiquette") or "").strip()}
                    for p in d.get("posts") or [] if isinstance(p, dict) and p.get("body")][:4]

        def check(posts):
            issues = [] if posts else ["no posts"]
            for i, p in enumerate(posts, 1):
                issues += check_post(p, i)
            return issues

        return self._run(user + "\nDraft the launch posts. Reply with the JSON object.", normalise, check)


def check_post(p: dict, i: int = 1) -> list[str]:
    text = f"{p['title']}\n{p['body']}"
    issues = [f"post {i}: {x}" for x in screen_copy(text)]
    if URL_RE.search(text.replace("{store_link}", "")):
        issues.append(f"post {i}: no links except the {{store_link}} placeholder")
    if IMPERSONATION_RE.search(text):
        issues.append(f"post {i}: must be written as the founder, not as a customer or bystander")
    if not re.search(r"\bI (built|made|created|have been building|am building|'ve built)|\bI'm building|\bmy (app|tool|product)\b",
                     text, re.I):
        issues.append(f"post {i}: must say clearly that the founder made it (e.g. 'I built')")
    return issues
