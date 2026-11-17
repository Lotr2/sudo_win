"""Policy Guard (policy as code): rules first, then a classifier on a different model from the writer.
Runs on the brief, on every founder chat message, on agent outputs that will be shown, and on every
outbox item. Also holds the Compliance Linter for Phase 4 policy drafts and store copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .decision_log import log_action
from .schemas import PolicyVerdict

# (rule id, category, pattern). Patterns are case-insensitive.
RULES: list[tuple[str, str, str]] = [
    ("R1", "fake_social_proof", r"\b(write|create|generate|post|add)\b[^.]{0,40}\b(reviews?|testimonials?|ratings?)\b"),
    ("R2", "fake_social_proof", r"\b(five|5)[- ]star (reviews?|ratings?)\b"),
    ("R3", "fake_social_proof", r"\b(fake|made[- ]up|invented)\b[^.]{0,30}\b(reviews?|testimonials?|sign-?ups?|users?|customers?)\b"),
    ("R4", "fake_social_proof", r"\b(join|trusted by|loved by|used by)\s+(over\s+)?\d[\d,]*\+?\s+(happy\s+)?(users|customers|founders|people|members)\b"),
    ("R5", "fake_urgency", r"\bcountdown\b|\btimer\b[^.]{0,20}\b(offer|sale|deal|expires?)\b"),
    ("R6", "fake_urgency", r"\b(only|just)\s+\d+\s+(left|spots?|seats?|copies)\b|\b(offer|price|deal|discount)\s+(ends|expires)\s+(soon|today|tonight|in)\b|\bhurry\b|\blast chance\b|\bact now\b"),
    ("R7", "income_promise", r"\bguarantee[sd]?\b[^.]{0,40}(\$\s?\d|income|revenue|profit|earn|money|results|success|sales)"),
    ("R8", "income_promise", r"\$\s?\d[\d,.]*\s?k?\s?(/|per|a)\s?(month|mo|week|day|year)\b"),
    ("R9", "income_promise", r"\b(make|earn)\s+(money|\$\d|passive income)|\bpassive income\b|\bget rich\b|\bpays for itself\b"),
    ("R10", "impersonation", r"\b(post|publish|send|reply|write)\b[^.]{0,30}\bas (the |a )?(founder|customer|user|buyer|ceo)\b|\bpretend(ing)? to be\b|\bpose as\b|\bon behalf of the founder\b|\bsign (it )?as (me|the founder)\b"),
    ("R11", "excluded_product", r"\b(crypto(currency)?|bitcoin|ethereum|nft|token sale|defi|forex|day[- ]trading|trading (bot|signals?)|stock (picks?|tips?)|investment advice|investing advice|loans?|lending|credit repair)\b"),
    ("R12", "excluded_product", r"\b(medical|diagnos\w*|symptom checker|therapy|therapist|mental health treatment|prescription|dosage|supplements?|diet plan for (diabetes|disease)|meal coach for (diabetes|disease)|weight[- ]loss (program|plan)|legal advice|lawyer|attorney|contract review service|immigration advice)\b"),
    ("R13", "excluded_product", r"\b(physical (product|goods)|t-?shirts?|mugs?|merch(andise)?|dropshipping|ship(ping)? (boxes|products)|inventory|handmade (goods|jewelry))\b"),
    ("R14", "money_movement", r"\brefund (the |this |that )?(customer|buyer|order|payment)\b[^.]{0,20}\b(now|immediately|right away)\b|\b(send|transfer|wire|move) (money|funds|\$\d)\b|\bcharge (the |their )?card\b"),
    ("R15", "credentials", r"\b(password|passcode|card number|credit card number|cvv|cvc|security code)s?\b[^.]{0,30}\b(store|save|collect|ask|enter|send|share|give)\b|\b(store|save|collect|ask for|enter|send|share|give)\b[^.]{0,30}\b(password|card number|cvv)s?\b"),
    ("R16", "credentials", r"\bcreate (an |a new )?accounts? (for|on behalf)\b"),
    # Paraphrases of the same requests (tests/trick_prompts.json holds one case per pattern family).
    ("R17", "fake_social_proof", r"\b(draft|make up|invent|fabricate|compose|come up with)\b[^.]{0,40}\b(reviews?|testimonials?|ratings?|"
                                 r"quotes? from (happy |satisfied )?(customers?|users?|clients?|buyers?))\b"),
    ("R18", "fake_social_proof", r"\b\d[\d,]*\+?\s+(happy\s+|satisfied\s+)?(users|customers|founders|people|members|designers|"
                                 r"freelancers|buyers|clients|students)\s+(already\s+)?(use|love|trust|bought|joined|rely on)\b"),
    ("R19", "fake_urgency", r"\b(offer|sale|deal|discount|price)\s+(ends|expires|ending)\b|\blimited[- ]time\b|"
                            r"\b(sale|discount|% off|offer)\b[^.]{0,30}\btimer\b|\btimer\b[^.]{0,30}\b(sale|discount|% off|offer)\b"),
    ("R20", "income_promise", r"\b(will|they'?ll|you'?ll|guaranteed to)\s+(earn|make)\s+(back|more|money|\$)|\bearn (it |the price )?back\b|"
                              r"\b(double|triple|10x|2x|3x)\s+(their|your|the)\s+(revenue|income|sales|profits?|clients|business)\b|"
                              r"\bpromise\b[^.]{0,40}\b(revenue|income|profits?|sales|results|clients|money)\b"),
    ("R21", "impersonation", r"\bas (if|though)\b[^.]{0,30}\b(came|comes|written|wrote|posted|sent|from)\b[^.]{0,25}\b(customer|user|"
                             r"buyer|client|someone else)\b|\bso (that )?it looks? (official|authentic|like it'?s from|like it came from)\b|"
                             r"\bfrom (a|an) (happy|satisfied|real) (customer|user|buyer|client)\b"),
    ("R22", "credentials", r"\b(store|save|keep|collect|ask|request|remember)\b[^.]{0,40}\b(passwords?|card (details|numbers?|"
                           r"info(rmation)?)|credit cards?|cvv|login details)\b"),
    ("R23", "excluded_product", r"\b(index funds?|etfs?|stock market|how to invest|invest(ing|ment)s? (in|course|guide|tips|strateg(y|ies)|plan)|"
                                r"real estate investing|tax[- ](saving|advice|strateg(y|ies)|planning|tips)|retirement planning|"
                                r"wealth management|financial advice|trading course)\b"),
    ("R24", "excluded_product", r"\b(medications?|medicines?|dose|doses|dosing|painkillers?|anxiety|depression|mental health|"
                                r"(without|instead of) (a |seeing a |going to a )?doctor|keto|diet (plan|ebook|e-book|program)|"
                                r"lose \d+\s?(kg|kilos?|lbs?|pounds)|weight[- ]loss|treatment plan)\b"),
    ("R25", "excluded_product", r"\b((rental|lease|tenancy|employment|partnership|prenup(tial)?|settlement) (agreements?|contracts?)|"
                                r"ndas?|non[- ]disclosure agreements?|divorce|last will|wills and trusts|estate planning|"
                                r"contract templates?|legal (documents?|templates?|forms?|guide)|visa applications?)\b"),
    ("R26", "excluded_product", r"\b(candles?|soaps?|handmade|hand-made|print[- ]on[- ]demand|printed (posters?|books?|planners?)|"
                                r"apparel|clothing|jewell?ery|etsy shop|ship(ped|ping)? to (customers|buyers))\b"),
]
_COMPILED = [(rid, cat, re.compile(p, re.I)) for rid, cat, p in RULES]

# Rules that apply to a founder's brief or chat; product copy also gets the copy-only rules (R4, R8).
EXCLUDED_PRODUCT_RULES = {"R11", "R12", "R13", "R23", "R24", "R25", "R26"}
REQUEST_RULES = {"R17", "R18", "R19", "R20", "R21", "R22"}
CONTEXT_RULES = {
    "brief": {"R1", "R2", "R3", "R5", "R6", "R7", "R10", "R14", "R15", "R16"} | REQUEST_RULES | EXCLUDED_PRODUCT_RULES,
    "chat": {"R1", "R2", "R3", "R5", "R7", "R10", "R14", "R15", "R16"} | REQUEST_RULES | EXCLUDED_PRODUCT_RULES,
    "output": {r[0] for r in RULES} - EXCLUDED_PRODUCT_RULES,
    "outbox": {r[0] for r in RULES} - EXCLUDED_PRODUCT_RULES,
}


@dataclass
class GuardResult:
    allowed: bool
    category: str
    rule: str | None
    reason: str
    classifier_model: str | None = None


# A promise rule must not fire on the disclaimer that denies the promise ("We make no guarantees of results").
NEGATION = re.compile(r"\b(no|not|never|without|none|cannot|can't|can not|don't|do not|doesn't|does not|won't|"
                      r"will not|make no|makes no|nor)\b[^.!?\n]{0,25}$", re.I)
NEGATABLE = {"income_promise", "credentials"}   # "we never store card numbers" is the opposite of the risk


def check_rules(text: str, context: str) -> GuardResult | None:
    active = CONTEXT_RULES.get(context, {r[0] for r in RULES})
    for rid, cat, rx in _COMPILED:
        if rid not in active:
            continue
        for m in rx.finditer(text):
            if cat in NEGATABLE and NEGATION.search(text[max(0, m.start() - 40):m.start()] + m.group(0)[:12]):
                continue
            return GuardResult(False, cat, rid, f"matched rule {rid} ({cat}): '{m.group(0)[:60]}'")
    return None


class PolicyGuard:
    def __init__(self, router=None):
        self.router = router

    def check(self, text: str, *, context: str, author_family: str | None = None,
              use_classifier: bool = True, subject: str = "") -> GuardResult:
        """Rules first; if no rule matches, a classifier on the model that did not write the text."""
        hit = check_rules(text, context)
        if hit:
            log_action("policy_guard", f"block_{context}", rule=hit.rule, category=hit.category, subject=subject or None)
            return hit
        if not use_classifier or self.router is None:
            log_action("policy_guard", f"pass_{context}", rule=None, subject=subject or None)
            return GuardResult(True, "ok", None, "no rule matched")
        verdict = self.router.call(
            "policy_classifier", {"context": context, "text": text[:6000]}, PolicyVerdict,
            action=f"classify_{context}", author_family=author_family)
        if verdict.allowed or verdict.category == "ok":
            return GuardResult(True, "ok", None, verdict.reason, classifier_model="classifier")
        log_action("policy_guard", f"block_{context}", rule="classifier", category=verdict.category,
                   subject=subject or None)
        return GuardResult(False, verdict.category, "classifier", verdict.reason, classifier_model="classifier")


# ---------------- Compliance Linter (Phase 4) ----------------

DISCLAIMER = "DRAFT: not legal advice"


def lint_policies(terms: str, privacy: str, refunds: str, refund_window_days: int, *,
                  delivery: str = "instant digital download") -> list[str]:
    """Deterministic checks on policy drafts. Returns violations (empty list = clean)."""
    violations: list[str] = []
    docs = {"terms": terms, "privacy": privacy, "refunds": refunds}
    for name, text in docs.items():
        if DISCLAIMER.lower() not in text.lower():
            violations.append(f"{name}: missing the '{DISCLAIMER}' label")
        hit = check_rules(text, "output")
        if hit and hit.category in {"income_promise", "fake_urgency", "fake_social_proof"}:
            violations.append(f"{name}: {hit.reason}")
    required = {
        "terms": ["licen", "refund", "contact"],
        "privacy": ["collect", "use", "contact"],
        "refunds": ["day", "request"],
    }
    for name, words in required.items():
        for w in words:
            if w not in docs[name].lower():
                violations.append(f"{name}: missing a section about '{w}'")
    if not re.search(rf"\b{refund_window_days}\b", refunds):
        violations.append(f"refunds: the refund window ({refund_window_days} days) is not stated in the refund policy")
    if refund_window_days <= 0 and "instant" in delivery:
        violations.append("refunds: a digital product needs a stated refund window")
    if re.search(r"\bno refunds?\b", refunds, re.I) and refund_window_days > 0:
        violations.append("refunds: says 'no refunds' but offers a refund window")
    if re.search(r"\bpassword|card number|cvv\b", privacy, re.I) and not re.search(r"(do not|never|don't) (collect|store)", privacy, re.I):
        violations.append("privacy: mentions passwords or card numbers without saying they are never stored")
    return violations
