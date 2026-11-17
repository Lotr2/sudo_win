# -*- coding: utf-8 -*-
"""Policy drafts: terms of service, privacy policy and refund policy.

These are fixed, plain-language templates filled in by code from facts the system already knows (the MVP spec,
the waitlist, the approved price) plus business details the founder types in. The model never writes legal text;
it only reviews the drafts (agents/policy_reviewer.py). Every draft is marked as not legal advice.

The checks in `check_policies` run on the files as they are on disk, so they also catch problems the founder
introduces when editing the drafts by hand.
"""

from __future__ import annotations

import re

from pricing import BILLING, billing_note, money

BANNER = ("> **DRAFT. This is not legal advice.** It was generated from a template and has not been reviewed by a "
          "lawyer. Have a qualified lawyer in {jurisdiction} review it before you rely on it or publish it.")
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z _/-]{2,}\]|\{\{.*?\}\}|\bTODO\b|\bTBD\b|(?i:lorem ipsum)")
FILES = {"terms": "phase4_step2_terms_of_service.md", "privacy": "phase4_step2_privacy_policy.md",
         "refunds": "phase4_step2_refund_policy.md"}


def capability(story: str) -> str:
    """'As a groomer, I can add clients so that ...' -> 'add clients'."""
    m = re.search(r"\bI can (.+?)(?:,?\s+so that\b|\.?$)", story, re.I)
    return (m.group(1) if m else story).strip().rstrip(".")


def facts_from(handoff: dict, pricing: dict, business: dict) -> dict:
    spec = handoff.get("spec") or {}
    evidence = handoff.get("evidence") or {}
    return {
        "product": spec.get("product_name") or "the product",
        "summary": spec.get("summary") or "",
        "features": [capability(s.get("story", "")) for s in spec.get("stories") or []],
        "out_of_scope": spec.get("out_of_scope") or [],
        "stores_data_in_browser": True,  # Phase 3 builds browser-only apps with localStorage
        "waitlist_collected": bool(evidence.get("signups")),
        "pricing": pricing,
        **business,
    }


def _header(title: str, f: dict) -> list[str]:
    return [f"# {title}: {f['product']}", "", BANNER.format(jurisdiction=f["jurisdiction"]), "",
            f"**Effective date:** {f['effective_date']}", "",
            f"{f['product']} is provided by {f['business_name']} (\"we\", \"us\"). "
            f"Contact: {f['contact_email']}.", ""]


def terms(f: dict) -> str:
    p = f["pricing"]
    lines = _header("Terms of service", f)
    lines += ["## 1. What the service is", "",
              f"{f['product']}: {f['summary']} It is an early version (MVP) and runs in your web browser.", ""]
    if f["features"]:
        lines += ["It currently lets you:", ""] + [f"- {x}" for x in f["features"]] + [""]
    if f["out_of_scope"]:
        lines += ["It does not currently include:", ""] + [f"- {x}" for x in f["out_of_scope"]] + [""]
    lines += ["## 2. Price and billing", "",
              f"The price is {money(p['price'], p['currency'])} ({BILLING[p['billing']]}). {billing_note(p)}",
              "The price you pay is the price shown before you pay. There are no hidden fees. Taxes may apply where "
              "the law requires them, and are shown before you pay.", "",
              "Payments are not being taken yet. When they are, a separate payment provider will handle your card "
              "details. We will never see or store your full card number.", ""]
    if p["billing"] != "one_time":
        lines += ["## 3. Cancelling", "",
                  "You can cancel at any time, in the same way you signed up or by emailing "
                  f"{f['contact_email']}. After you cancel you won't be charged again, and you keep access until the "
                  "end of the period you already paid for.", ""]
    n = 4 if p["billing"] != "one_time" else 3
    lines += [f"## {n}. Refunds", "", f"See the refund policy. In short: "
              + (f"you can ask for a full refund within {p['refund_days']} days of paying." if p["refund_days"]
                 else "payments are not refundable."), "",
              f"## {n + 1}. Your data", "",
              "Data you enter is stored in your own browser on your device. See the privacy policy.", "",
              f"## {n + 2}. Acceptable use", "",
              "Don't use the service to break the law, to harm others, or to try to disrupt or break the service.", "",
              f"## {n + 3}. No promises about results", "",
              f"{f['product']} is a tool. We don't promise any particular result, income or saving from using it. "
              "It is provided as it is, and as an early version it may have mistakes. Keep your own copy of anything "
              "important.", "",
              f"## {n + 4}. Liability", "",
              "To the extent the law allows, our total liability to you is limited to the amount you paid us in the "
              "12 months before the claim. Nothing in these terms limits rights you have under consumer law that "
              "cannot be limited.", "",
              f"## {n + 5}. Changes", "",
              "If we change these terms, we will tell you before the change takes effect. If you don't agree, you "
              "can stop using the service" + (" and cancel" if p["billing"] != "one_time" else "") + ".", "",
              f"## {n + 6}. Governing law", "", f"These terms are governed by the laws of {f['jurisdiction']}.", ""]
    return "\n".join(lines)


def privacy(f: dict) -> str:
    lines = _header("Privacy policy", f)
    lines += ["## What we collect", ""]
    if f["stores_data_in_browser"]:
        lines += [f"**Data you enter in {f['product']}** is stored only in your web browser on your device "
                  "(browser local storage). It is not sent to us, and we can't see it. Clearing your browser data "
                  "deletes it.", ""]
    if f["waitlist_collected"]:
        lines += ["**Waitlist email addresses.** If you joined our waitlist, we store your email address and when "
                  f"you joined. We use it only to contact you about {f['product']}.", ""]
    lines += ["**Payment details.** We don't take payments yet. When we do, a separate payment provider will "
              "process them, and we will never see or store your full card number.", "",
              "We don't use advertising trackers or analytics cookies, and we don't sell or share your data.", "",
              "## How long we keep it", "",
              "Data in your browser stays until you delete it."
              + (" Waitlist emails are kept until you ask us to remove them or we stop working on the product."
                 if f["waitlist_collected"] else ""), "",
              "## Your choices", "",
              f"You can ask what we hold about you, or ask us to delete it, by emailing {f['contact_email']}."
              + (" You can also reply to any waitlist email to be removed." if f["waitlist_collected"] else ""), "",
              "## Changes", "", "If we change how we handle data, we will update this policy and its effective date "
              "before the change.", ""]
    return "\n".join(lines)


def refunds(f: dict) -> str:
    p = f["pricing"]
    lines = _header("Refund policy", f)
    if p["refund_days"]:
        lines += ["## Full refund", "",
                  f"If {f['product']} isn't right for you, email {f['contact_email']} within {p['refund_days']} days "
                  "of paying and we will refund you in full. You don't have to give a reason.", ""]
        if p["billing"] != "one_time":
            lines += [f"This applies to each payment: you can ask within {p['refund_days']} days of any renewal.", ""]
    else:
        lines += ["## No refunds", "", "Payments are not refundable, except where the law requires a refund.", ""]
    lines += ["## How refunds are paid", "",
              "Refunds go back to the original payment method. Payments aren't being taken yet; when they are, "
              "every refund will be reviewed and approved by a person. Nothing is refunded automatically.", "",
              "## Your legal rights", "", "This policy doesn't affect any refund rights you have under the law.", ""]
    return "\n".join(lines)


def render_all(facts: dict) -> dict[str, str]:
    return {"terms": terms(facts), "privacy": privacy(facts), "refunds": refunds(facts)}


def check_policies(texts: dict[str, str], facts: dict) -> list[str]:
    """Deterministic checks on the drafts as they are now (including founder edits). Empty list = pass."""
    p = facts["pricing"]
    issues = []
    for name in FILES:
        text = texts.get(name) or ""
        if not text.strip():
            issues.append(f"{name}: missing")
            continue
        if "not legal advice" not in text.lower():
            issues.append(f"{name}: the 'not legal advice' notice was removed")
        if PLACEHOLDER.search(text):
            issues.append(f"{name}: has unfilled placeholder text ({PLACEHOLDER.search(text).group(0)})")
        if facts["contact_email"] not in text:
            issues.append(f"{name}: contact email {facts['contact_email']} is missing")
    t, pr, r = (texts.get(k) or "" for k in ("terms", "privacy", "refunds"))
    if money(p["price"], p["currency"]) not in t:
        issues.append(f"terms: price {money(p['price'], p['currency'])} does not match the approved price")
    if p["billing"] != "one_time" and not (re.search(r"\bcancel", t, re.I) and re.search(r"\brenew", t, re.I)):
        issues.append("terms: a subscription needs clear renewal and cancellation terms")
    if p["refund_days"]:
        for name, text in (("terms", t), ("refunds", r)):
            if f"{p['refund_days']} days" not in text:
                issues.append(f"{name}: refund window does not match the approved {p['refund_days']} days")
    elif "not refundable" not in r.lower():
        issues.append("refunds: must say clearly that payments are not refundable")
    if facts["waitlist_collected"] and "waitlist" not in pr.lower():
        issues.append("privacy: waitlist emails were collected but are not explained")
    if facts["stores_data_in_browser"] and "browser" not in pr.lower():
        issues.append("privacy: must explain that data is stored in the user's browser")
    if re.search(r"\b(we|you) will (earn|make|save) \$?\d|\bguaranteed? (results|income|success)", t + r, re.I):
        issues.append("terms/refunds: promise of results or income")
    return issues
