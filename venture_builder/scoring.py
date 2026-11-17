"""Scorer [code]: weighted-sum scoring of the idea variants (weights in config/scoring.toml).
Only SOURCED web evidence counts; founder_reported, simulated and assumed evidence weigh 0."""

from __future__ import annotations

from .ledger import EvidenceLedger
from .schemas import Variant, VariantScore

DEFAULTS = {"hours_per_week": 5.0, "budget_usd": 100.0, "skills": [], "market": "general online buyers"}


def _sourced_web(findings: list[dict], kinds: set[str], ledger: EvidenceLedger) -> list:
    items = []
    for f in findings:
        if f["kind"] in kinds and f.get("label") == "SOURCED":
            for eid in f["evidence_ids"]:
                it = ledger.get(eid)
                if it and it.type == "web" and ledger.gate_weight(eid) > 0:
                    items.append(it)
    return items


def skill_coverage(needed: list[str], have: list[str]) -> float:
    if not needed:
        return 1.0
    have_l = [h.lower() for h in have]
    hits = sum(1 for n in needed if any(n.lower() in h or h in n.lower() for h in have_l if h))
    return hits / len(needed)


def score_variant(v: Variant, findings: list[dict], profile: dict, ledger: EvidenceLedger, scoring: dict,
                  templates: list[str]) -> VariantScore:
    w, cap = scoring["weights"], scoring["caps"]["pain_signals"]
    hours = float(profile.get("hours_per_week") or DEFAULTS["hours_per_week"])
    budget = float(profile.get("budget_usd") if profile.get("budget_usd") is not None else DEFAULTS["budget_usd"])
    skills = profile.get("skills") or []

    pain_items = _sourced_web(findings, {"pain", "demand"}, ledger)
    urls = {i.url for i in pain_items}
    domains = {i.domain for i in pain_items}
    pain = min(len(urls), cap) / cap

    coverage = skill_coverage(v.skills_needed, skills)
    budget_ok = v.budget_needed_usd <= budget
    month_hours = hours * 4
    hours_ratio = min(1.0, month_hours / v.est_build_hours) if v.est_build_hours > 0 else 1.0
    founder_fit = round((coverage + (1.0 if budget_ok else 0.0) + hours_ratio) / 3, 3)
    fit_pass = budget_ok and v.est_build_hours <= month_hours and coverage >= 0.5

    channels = {i.url for i in _sourced_web(findings, {"channel"}, ledger)}
    reachability = min(len(channels), 3) / 3

    in_templates = v.product_form in templates
    buildability = 1.0 if in_templates and v.est_build_hours <= month_hours else (0.5 if in_templates else 0.0)

    complaints = len({i.url for i in _sourced_web(findings, {"complaint"}, ledger)})
    alternatives = len({i.url for i in _sourced_web(findings, {"alternative", "price"}, ledger)})
    competition_gap = round(0.6 * min(1.0, complaints / 3) + 0.4 * (1 - min(1.0, alternatives / 5)), 3)

    score = (w["pain"] * pain + w["founder_fit"] * founder_fit + w["reachability"] * reachability
             + w["buildability"] * buildability + w["competition_gap"] * competition_gap)
    return VariantScore(
        variant_id=v.id, pain=round(pain, 3), founder_fit=founder_fit, reachability=round(reachability, 3),
        buildability=buildability, competition_gap=competition_gap, score=round(score, 3),
        pain_signals=len(urls), pain_domains=len(domains), fit_rules_pass=fit_pass,
        inputs={"hours_per_week": hours, "budget_usd": budget, "skill_coverage": round(coverage, 2),
                "budget_ok": budget_ok, "build_hours": v.est_build_hours, "complaints": complaints,
                "alternatives": alternatives, "channels": len(channels)},
    )


def score_all(variants: list[Variant], findings: dict, profile: dict, ledger: EvidenceLedger, scoring: dict,
              templates: list[str]) -> list[VariantScore]:
    return [score_variant(v, findings.get(v.id, []), profile, ledger, scoring, templates) for v in variants]
