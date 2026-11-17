"""Phase 4 · Commercial setup: pipes and filters with a linter loop. No payments of any kind.

Liaison asks about price sensitivity and refunds → Pricing Analyst (sourced anchors) → Policy Drafter →
Compliance Linter [code] (violations loop back) → Gate 4 → founder signs off.
"""

from __future__ import annotations

from .. import gates
from ..agents import pricing_analyst, policy_drafter
from ..clarifier import _as_number
from ..context import Ask, Context, Next
from ..policy_guard import lint_policies
from .common import decision, gate_markdown, present_gate, stop_run


def _price_evidence(ctx: Context) -> list[str]:
    ids = []
    for f in ctx.dossier.findings.get(ctx.dossier.chosen_variant, []):
        if f["kind"] in {"price", "alternative"}:
            ids += f["evidence_ids"]
    return list(dict.fromkeys(i for i in ids if ctx.ledger.get(i)))


def p4_start(ctx: Context, answer) -> Ask:
    ctx.dossier.phase = 4
    ctx.note("Phase 4 · Commercial setup started")
    from .common import kickoff
    kickoff(ctx, "Commercial setup: price from sourced anchors, then policy drafts checked by code",
            ["competitor prices found in research", "a refund window fits a digital product"])
    q = ("Before pricing: what would feel too expensive for your customers, and how long a refund window "
         "are you comfortable with for a digital product?")
    ctx.say(q, kind="question")
    return Ask("question", {"question": q, "why": "pricing and refund preferences", "optional": True},
               resume_to="p4_start_answer", timeout=ctx.question_timeout())


def p4_start_answer(ctx: Context, answer) -> Next:
    text = answer if isinstance(answer, str) else (answer or {}).get("text") if answer else None
    if text:
        ctx.dossier.founder_inputs.append({"phase": 4, "type": "founder_reported", "text": text})
    return Next("p4_pricing")


def _sourced_range(ctx: Context, ids: list[str]) -> tuple[float, float] | None:
    prices = []
    for i in ids:
        item = ctx.ledger.get(i)
        if item and item.type == "web":
            for m in __import__("re").finditer(r"\$\s?(\d{1,4}(?:\.\d{2})?)", item.snippet):
                prices.append(float(m.group(1)))
    prices = [p for p in prices if 1 <= p <= 2000]
    return (min(prices), max(prices)) if len(prices) >= 2 else None


def p4_pricing(ctx: Context, answer) -> Next:
    d = ctx.dossier
    ids = _price_evidence(ctx)
    payload = {"product": d.spec.model_dump(include={"title", "summary", "product_form", "features"}),
               "price_evidence": ctx.ledger.listing(ids),
               "founder_preferences": [x["text"] for x in d.founder_inputs if x.get("phase") == 4]}
    d.pricing = pricing_analyst.run(ctx, payload, ids)
    claim = ctx.ledger.check_claim(d.pricing.reasoning, d.pricing.evidence_ids) if d.pricing.evidence_ids else None
    rng = _sourced_range(ctx, ids)
    md = ["# Phase 4 · Step 1: Pricing", "", f"**Price:** {d.pricing.currency} {d.pricing.price:g}", ""]
    if d.pricing.tiers:
        md += ["| Tier | Price | Includes |", "|---|---|---|"]
        md += [f"| {t.name} | {t.price:g} | {'; '.join(t.includes)} |" for t in d.pricing.tiers]
    label = "SOURCED: " + ", ".join(claim.sources) if claim and claim.label == "SOURCED" else "ASSUMPTION"
    md += ["", "## Reasoning", "", d.pricing.reasoning, "", f"Source: {label}", "",
           f"Sourced competitor price range: {f'${rng[0]:g}-${rng[1]:g}' if rng else 'not enough sourced prices (ASSUMPTION)'}",
           "", "No revenue forecast is made, and no income is promised."]
    d.build["price_range"] = list(rng) if rng else None
    ctx.store.write_output("phase4_step1_pricing.md", "\n".join(md) + "\n")
    return Next("p4_policies")


def p4_policies(ctx: Context, answer) -> Next:
    d = ctx.dossier
    prefs = " ".join(x["text"] for x in d.founder_inputs if x.get("phase") == 4)
    window = int(_as_number(prefs) or 14) if "day" in prefs.lower() else 14
    violations: list[str] = []
    for attempt in range(1, 4):
        payload = {"product": d.spec.title, "product_form": d.spec.product_form, "price": d.pricing.price,
                   "currency": d.pricing.currency, "delivery": "instant digital download after payment",
                   "refund_window_days": window, "violations_to_fix": violations,
                   "required_label": "DRAFT: not legal advice"}
        drafts = policy_drafter.run(ctx, payload)
        violations = lint_policies(drafts.terms, drafts.privacy, drafts.refunds, drafts.refund_window_days)
        ctx.note(f"Compliance Linter pass {attempt}: {len(violations)} violation(s)")
        if not violations:
            break
    d.policies, d.lint = drafts, violations
    md = ["# Phase 4 · Step 2: Policies (DRAFT: not legal advice)", "",
          "These are drafts for this product, generated from templates and checked by code. They are not legal "
          "advice.", "", "## Terms of service", "", drafts.terms, "", "## Privacy policy", "", drafts.privacy, "",
          "## Refund policy", "", drafts.refunds, "", "## Compliance Linter", ""]
    md += [f"- {v}" for v in violations] or ["- Clean: no violations."]
    ctx.store.write_output("phase4_step2_policies.md", "\n".join(md) + "\n")
    return Next("p4_gate")


def p4_gate(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    rng = tuple(d.build["price_range"]) if d.build.get("price_range") else None
    res = gates.gate4(violations=d.lint, price=d.pricing.price, price_range=rng,
                      price_justified=bool(d.pricing.evidence_ids), t=ctx.t)
    memo = {"summary": f"Gate 4 recommends {res.outcome.upper()}.",
            "explanation": "Code filled this memo from the linter report and the price check.",
            "what_would_change_it": ["Fixing the remaining violations"] if d.lint else []}
    md = gate_markdown(ctx, res, memo, "What does it cost, and on what terms?", "gate4")
    ctx.say("Signing off here approves the price and the policy drafts for this product (DRAFT: not legal advice).")
    return present_gate(ctx, res, md, "p4_gate_decided")


def p4_gate_decided(ctx: Context, answer) -> Next:
    d = ctx.dossier
    rec = decision(ctx, 4)
    if rec["decision"] == "stop":
        return Next(stop_run(ctx, 4, "The founder rejected the commercial setup."))
    if rec["decision"] == "repeat":
        d.bump("gate4_repeat")
        if rec.get("note"):
            d.founder_inputs.append({"phase": 4, "type": "founder_reported", "text": rec["note"]})
        return Next("p4_pricing")
    return Next("p5_start")


NODES = {"p4_start": p4_start, "p4_start_answer": p4_start_answer, "p4_pricing": p4_pricing,
         "p4_policies": p4_policies, "p4_gate": p4_gate, "p4_gate_decided": p4_gate_decided}
