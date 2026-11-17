"""Never-do rules: request screen (what we are asked), output lint (what we produce), excluded categories.

Rules are deterministic regexes. A variant may add an LLM verifier on top (it can only refuse or block more,
never less).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_I = re.IGNORECASE

# Requests the system must refuse (judges try these as trick prompts).
REQUEST_RULES: dict[str, list[str]] = {
    "fake_reviews": [
        r"\b(write|create|generate|make|post|invent|fake|draft|produce)\b.{0,60}\b(reviews?|testimonials?|ratings?|endorsements?)\b",
        r"\b(five|5)[- ]?star\b",
        r"\bcustomer (stories|quotes)\b.{0,40}\b(make up|invent|fake|imagine)\b",
    ],
    "fake_signups_or_metrics": [
        r"\b(fake|invent|inflate|make up|pad)\b.{0,40}\b(sign-?ups?|users|customers|subscribers|waitlist|numbers|metrics|traction)\b",
    ],
    "urgency": [r"\bcountdown\b", r"\b(fake|false|artificial)\b.{0,20}\b(urgency|scarcity)\b", r"\bonly \d+ (left|spots|copies)\b"],
    "income_promise": [
        r"\b(guarantee|promise)\w*\b.{0,60}\b(income|earnings|profit|revenue|money|success|results)\b",
        r"\b(earn|make)\s+\$?\d[\d,.]*\s*k?\s*(/|per|a)\s*(month|week|day|year)\b",
        r"\bpassive income\b|\bget rich\b",
    ],
    "impersonation": [
        r"\b(pose|posing|pretend\w*|impersonat\w*|masquerad\w*)\b.{0,40}\b(as|to be)\b",
        r"\b(sign|post|write|reply)\w*\b.{0,30}\bas (the )?(founder|customer|ceo)\b",
        r"\bas seen on\b",
    ],
    "money_or_credentials": [
        r"\b(store|save|collect|keep|log)\b.{0,40}\b(card numbers?|credit cards?|passwords?|cvv|bank details)\b",
        r"\b(move|transfer|wire|withdraw)\b.{0,20}\b(money|funds|payouts?)\b",
        r"\b(charge|bill)\b.{0,30}\b(card|customers?|buyers?)\b",
        r"\bcreate\b.{0,20}\baccounts?\b.{0,20}\b(for|on behalf)\b",
    ],
    "paywall": [r"\bpaywall\b"],
}

# Patterns that must never appear in produced (outward) text.
OUTPUT_RULES: dict[str, list[str]] = {
    "income_promise": REQUEST_RULES["income_promise"] + [r"\bguaranteed\b"],
    "fake_social_proof": [r"★★★", r"\b[45](\.\d)?\s*/\s*5\b", r"\b(five|5)[- ]star\b", r"^#+\s*testimonials?\b",
                          r"\b\d[\d,]+\+?\s+(happy |satisfied )?(customers|users|founders|subscribers)\b",
                          r"\bjoin(ed)? (over )?\d[\d,]+\b"],
    "urgency": [r"\bcountdown\b", r"\bhurry\b", r"\blimited[- ]time\b", r"\b(offer|sale|deal|price) ends\b",
                r"\bonly \d+ (left|spots|copies)\b", r"\blast chance\b"],
    "impersonation": [r"\bas seen on\b", r"\bofficial (partner|product) of\b"],
    "credentials": [r"\b(card number|cvv|password)\b"],
}

EXCLUDED_CATEGORIES: dict[str, list[str]] = {
    "physical_goods": [r"\bphysical (product|goods)\b", r"\bship(ping)? (the )?(product|goods|orders)\b", r"\bt-?shirts?\b",
                       r"\bmerch(andise)?\b", r"\bhardware\b", r"\bprinted (book|copies)\b"],
    "financial_or_crypto": [r"\bcrypto\w*\b", r"\bbitcoin\b", r"\bnft\b", r"\btrading (signals?|bot)\b", r"\binvest(ing|ment)\b",
                            r"\bstock picks?\b", r"\bforex\b", r"\bloans?\b", r"\binsurance\b", r"\bbudget(ing)? (app|template|planner)\b",
                            r"\bpersonal finance\b", r"\btax (advice|filing)\b"],
    "health_advice": [r"\bdiagnos\w*\b", r"\bmedical\b", r"\btreatment\b", r"\bsupplements?\b", r"\bweight[- ]loss\b",
                      r"\b(therapy|therapist)\b", r"\bmental health\b", r"\bdiet plan\b", r"\bfitness (plan|program)\b", r"\bsymptoms?\b"],
    "legal_advice": [r"\blegal advice\b", r"\blawsuits?\b", r"\bcontract (review|templates?)\b", r"\bimmigration\b", r"\bwill and testament\b"],
}


@dataclass
class Finding:
    rule: str
    pattern: str
    excerpt: str


@dataclass
class ScreenResult:
    allowed: bool
    findings: list[Finding] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return sorted({f.rule for f in self.findings})


_NEGATION = re.compile(r"\b(no|not|never|without|don'?t|do not|avoid|refuse|won'?t|nothing is|isn'?t)\b[^.\n]{0,25}$", _I)


def _scan(text: str, rules: dict[str, list[str]], flags=_I | re.MULTILINE) -> list[Finding]:
    """First non-negated match per rule ("no fake reviews" is a value statement, not a request)."""
    out = []
    for rule, patterns in rules.items():
        hit = None
        for p in patterns:
            for m in re.finditer(p, text, flags):
                if not _NEGATION.search(text[max(0, m.start() - 40):m.start()]):
                    hit = (p, m)
                    break
            if hit:
                break
        if hit:
            p, m = hit
            s = max(0, m.start() - 30)
            out.append(Finding(rule, p, text[s:m.end() + 30].replace("\n", " ")))
    return out


def screen_request(text: str) -> ScreenResult:
    """Rule-based screen of a founder/user request. Refuses never-do requests and excluded products."""
    findings = _scan(text, REQUEST_RULES) + [Finding(f"excluded:{f.rule}", f.pattern, f.excerpt)
                                              for f in _scan(text, EXCLUDED_CATEGORIES)]
    return ScreenResult(allowed=not findings, findings=findings)


def lint(text: str) -> list[Finding]:
    """Findings in produced text. Any finding blocks an outward action."""
    return _scan(text, OUTPUT_RULES)


def excluded_category(text: str) -> list[Finding]:
    return _scan(text, EXCLUDED_CATEGORIES)
