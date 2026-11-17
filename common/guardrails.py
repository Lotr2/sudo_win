"""Deterministic checks that run before and alongside the model, so safety does not depend on the model alone."""

from __future__ import annotations

import re

# Requests the system must never fulfil, whatever the model says.
FORBIDDEN_REQUESTS = {
    "fake social proof": r"\b(fake|made[- ]up|invented|fabricat\w*|generate|write)\b.{0,40}\b(reviews?|testimonials?|sign-?ups?|ratings?)\b",
    "fake urgency": r"\b(fake|false)\b.{0,30}\b(urgency|scarcity|countdown)\b|\bcountdown timer\b",
    "income promise": r"\b(guarantee\w*|promise\w*)\b.{0,40}\b(income|revenue|profit|success|earnings|returns?)\b",
    "impersonation": r"\b(pretend|pose|impersonat\w*)\b.{0,40}\b(as|to be)\b",
    "credential storage": r"\b(store|save|keep|collect)\b.{0,30}\b(passwords?|card numbers?|credit cards?|cvv)\b",
}

# Product categories excluded by the competition rules.
EXCLUDED_CATEGORIES = {
    "physical goods": r"\b(physical (product|goods)|sell(ing)? (merch|t-?shirts|clothing|apparel|candles|jewel(le)?ry|furniture|cosmetics)|dropshipping)\b",
    "financial / investment / crypto": r"\b(crypto(currency)?|bitcoin|ethereum|nfts?|token sale|defi|trading (bot|signals?)|forex|stock picks?|investment advice|personal loans?)\b",
    "health advice": r"\b(medical advice|diagnos(e|is|ing) (patients|symptoms|users)|treatment plans?|prescriptions?|supplements?|diet plans?|weight[- ]loss (plan|program))\b",
    "legal advice": r"\b(legal advice|(draft|write|review)\w* (contracts|wills|legal documents) for)\b",
}


def screen_request(text: str) -> list[str]:
    """Return the names of any forbidden requests found in founder input."""
    return [name for name, pat in FORBIDDEN_REQUESTS.items() if re.search(pat, text, re.IGNORECASE)]


def screen_category(text: str) -> list[str]:
    """Return excluded product categories the idea text appears to fall into."""
    return [name for name, pat in EXCLUDED_CATEGORIES.items() if re.search(pat, text, re.IGNORECASE)]


# Patterns that must never appear in public-facing copy the system writes (landing pages, posts).
FORBIDDEN_COPY = {
    "testimonial or review": r"(“|\")[^\"”]{10,}(”|\")\s*[-–—]\s*[A-Z]|\b(testimonials?|5[- ]star|five[- ]star|rated \d)\b|\bwhat (our )?(users|customers|people) (are )?say",
    "fake social proof": r"\b(join|trusted by|used by|loved by)\s+(over\s+)?\d[\d,.]*\+?\s*(k\b|people|users|customers|teams|founders|businesses|others)|\b\d[\d,]*\+?\s+(people|users|customers) (have )?(already )?(joined|signed up)",
    "fake urgency or scarcity": r"\b(only \d+ (spots?|seats?|places?) (left|remaining)|limited (time|spots|seats)|(offer )?ends (soon|tonight|today)|hurry|act now|don'?t miss out|last chance|countdown)\b",
    "income or success promise": r"\b(guarantee\w*|risk[- ]free|you will (earn|make)|make \$?\d+|double your (income|revenue|sales))\b",
}


def screen_copy(text: str) -> list[str]:
    """Return the names of any forbidden patterns found in public-facing copy."""
    return [name for name, pat in FORBIDDEN_COPY.items() if re.search(pat, text, re.IGNORECASE)]


REFUSAL = (
    "I can't help with that. This system never creates fake reviews, testimonials, sign-ups or urgency, "
    "never promises income or success, never impersonates anyone, and never stores passwords or card details."
)
