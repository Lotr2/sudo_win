"""Pricing: validate the proposed plan, work out break-even, and build honest pricing-page text.

The price line, the billing note and the refund note are written by code from the numbers, never by the model,
so how often you are charged and how to cancel can't be hidden or reworded.
"""

from __future__ import annotations

import math
import re

from common.guardrails import screen_copy

BILLING = {"one_time": "one-time payment", "monthly": "per month", "yearly": "per year"}
FAKE_DISCOUNT = re.compile(r"\bwas\s+[$€£]?\d|~~|\b\d{1,3}\s*%\s*off\b|\bsave\s+\d{1,3}\s*%|\bsale\b|\bregular(ly)? [$€£]?\d", re.I)


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def normalise(data: dict) -> dict:
    billing = str(data.get("billing") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if billing in ("one_off", "onetime", "lifetime"):
        billing = "one_time"
    breakdown = [{"item": str(b.get("item") or ""), "amount": round(_num(b.get("amount")), 2)}
                 for b in data.get("monthly_costs_breakdown") or [] if isinstance(b, dict)]
    assumptions = []
    for a in data.get("assumptions") or []:
        a = str(a).strip()
        if a:
            assumptions.append(a if a.upper().startswith("ASSUMPTION") else f"ASSUMPTION: {a}")
    return {
        "billing": billing,
        "price": round(_num(data.get("price")), 2),
        "currency": str(data.get("currency") or "USD").strip().upper()[:3],
        "refund_days": int(_num(data.get("refund_days"), 14)),
        "monthly_costs": round(_num(data.get("monthly_costs")), 2),
        "monthly_costs_breakdown": breakdown,
        "value_metric": str(data.get("value_metric") or "").strip(),
        "rationale": str(data.get("rationale") or "").strip(),
        "anchors": [{"alternative": str(a.get("alternative") or ""), "price": str(a.get("price") or ""),
                     "status": "ASSUMPTION"} for a in data.get("anchors") or [] if isinstance(a, dict)],
        "price_test": str(data.get("price_test") or "").strip(),
        "headline": str(data.get("headline") or "").strip(),
        "includes": [str(x).strip() for x in data.get("includes") or [] if str(x).strip()][:8],
        "assumptions": assumptions,
    }


def validate(p: dict) -> list[str]:
    problems = []
    if p["billing"] not in BILLING:
        problems.append(f"billing must be one of: {', '.join(BILLING)}")
    if not 0 < p["price"] <= 10000:
        problems.append("price must be more than 0 and at most 10,000")
    if not re.fullmatch(r"[A-Z]{3}", p["currency"]):
        problems.append("currency must be a 3-letter code like USD")
    if not 0 <= p["refund_days"] <= 90:
        problems.append("refund window must be 0-90 days")
    if p["monthly_costs"] < 0:
        problems.append("monthly costs cannot be negative")
    copy = " ".join([p["headline"], *p["includes"]])
    for issue in screen_copy(copy):
        problems.append(f"pricing copy breaks the rules: {issue}")
    if FAKE_DISCOUNT.search(copy):
        problems.append("pricing copy has a discount or 'was' price: no fake anchors")
    return problems


def monthly_revenue_per_customer(p: dict) -> float:
    return {"monthly": p["price"], "yearly": p["price"] / 12}.get(p["billing"], p["price"])


def break_even(p: dict) -> dict:
    """Customers (or one-time sales per month) needed to cover the running costs."""
    per = monthly_revenue_per_customer(p)
    needed = math.ceil(p["monthly_costs"] / per) if per > 0 and p["monthly_costs"] > 0 else 0
    unit = "one-time sales per month" if p["billing"] == "one_time" else "paying customers"
    return {"needed": needed, "unit": unit, "monthly_revenue_per_customer": round(per, 2)}


def money(amount: float, currency: str) -> str:
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency)
    text = f"{amount:,.2f}".removesuffix(".00")
    return f"{sym}{text}" if sym else f"{text} {currency}"


def price_line(p: dict) -> str:
    return f"{money(p['price'], p['currency'])} {BILLING[p['billing']]}"


def billing_note(p: dict) -> str:
    if p["billing"] == "one_time":
        return "You pay once. There is no subscription and nothing renews."
    period = "month" if p["billing"] == "monthly" else "year"
    return (f"Renews every {period} at {money(p['price'], p['currency'])} until you cancel. "
            f"You can cancel any time, and you won't be charged again.")


def refund_note(p: dict) -> str:
    if p["refund_days"] == 0:
        return "Payments are not refundable, except where the law requires a refund."
    return f"If it isn't right for you, ask for a full refund within {p['refund_days']} days of paying."
