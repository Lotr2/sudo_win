"""Scorer [code]: weighted-sum scoring of the idea variants (weights in config/scoring.toml).
Only SOURCED web evidence counts; founder_reported, simulated and assumed evidence weigh 0."""

from __future__ import annotations

from .ledger import EvidenceLedger
from .schemas import Variant, VariantScore

DEFAULTS = {"hours_per_week": 5.0, "budget_usd": 100.0, "skills": [], "market": "general online buyers"}


# Evidence grading: how much one source counts towards "people have this problem".
TYPE_WEIGHT = {"first_hand": 1.0, "question": 1.0, "article": 0.8, "other": 0.8, "listing": 0.0}
OFF_MARKET_WEIGHT = 0.6   # about the topic in general, not the founder's market


def graded_problem_sources(findings: list[dict], ledger: EvidenceLedger) -> dict[str, dict]:
    """url -> {weight, type, on_market, domain} for sourced web evidence of pain or demand (best grade per url)."""
    out: dict[str, dict] = {}
    for f in findings:
        if f["kind"] not in {"pain", "demand"} or f.get("label") != "SOURCED":
            continue
        typ = f.get("source_type") or "other"
        on_market = f.get("about_market", True)
        w = TYPE_WEIGHT.get(typ, 0.8) * (1.0 if on_market else OFF_MARKET_WEIGHT)
        for eid in f["evidence_ids"]:
            it = ledger.get(eid)
            if not it or it.type != "web" or ledger.gate_weight(eid) <= 0 or not it.url or w <= 0:
                continue
            if it.url not in out or w > out[it.url]["weight"]:
                out[it.url] = {"weight": w, "type": typ, "on_market": bool(on_market), "domain": it.domain}
    return out


def _sourced_web(findings: list[dict], kinds: set[str], ledger: EvidenceLedger) -> list:
    items = []
    for f in findings:
        if f["kind"] in kinds and f.get("label") == "SOURCED":
            for eid in f["evidence_ids"]:
                it = ledger.get(eid)
                if it and it.type == "web" and ledger.gate_weight(eid) > 0:
                    items.append(it)
    return items


# Skills that cover each other: a founder who "does web dev" can handle "database design" or "front-end".
SKILL_FAMILIES = [
    {"web", "web dev", "web development", "developer", "development", "programming", "coding", "software",
     "javascript", "python", "front end", "frontend", "front-end", "back end", "backend", "back-end", "full stack",
     "database", "database design", "sql", "api", "html", "css", "react", "web layout", "basic web layout",
     "no-code", "website", "app development"},
    {"design", "graphic design", "ui", "ux", "ui/ux", "ui/ux design", "ui design", "ux design", "figma",
     "visual design", "branding", "layout", "template design", "basic design", "canva", "illustration"},
    {"marketing", "digital marketing", "social media", "seo", "content", "content writing", "copywriting",
     "marketing copy", "advertising", "growth", "community", "email marketing", "branding", "sales", "outreach"},
    {"writing", "content writing", "copywriting", "editing", "blogging", "documentation", "technical writing",
     "research", "storytelling"},
    {"teaching", "training", "coaching", "tutoring", "course creation", "explaining"},
    {"data", "data analysis", "analytics", "spreadsheets", "excel", "google sheets", "statistics"},
    {"business", "operations", "project management", "management", "customer service", "negotiation"},
]


def _norm(skill: str) -> str:
    return " ".join(str(skill).lower().replace("_", " ").split())


def _family(skill: str) -> set[int]:
    s = _norm(skill)
    words = set(s.replace("/", " ").replace("-", " ").split())
    found = set()
    for i, fam in enumerate(SKILL_FAMILIES):
        if s in fam or any(w in fam for w in words) or any(f in s for f in fam if len(f) > 3):
            found.add(i)
    return found


def match_skills(needed: list[str], have: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split needed skills into (covered by the founder, learnable basics, missing)."""
    have_n = [_norm(h) for h in have if h]
    have_fams = set().union(*[_family(h) for h in have_n]) if have_n else set()
    covered, learnable, missing = [], [], []
    for n in needed:
        nn = _norm(n)
        direct = any(nn in h or h in nn for h in have_n)
        if direct or (_family(nn) & have_fams):
            covered.append(n)
        elif nn.startswith(("basic ", "simple ", "some ")):
            learnable.append(n)
        else:
            missing.append(n)
    return covered, learnable, missing


def skill_coverage(needed: list[str], have: list[str]) -> float:
    """Share of the needed skills the founder has; a 'basic' skill they lack counts half (it can be learned)."""
    if not needed:
        return 1.0
    covered, learnable, _ = match_skills(needed, have)
    return (len(covered) + 0.5 * len(learnable)) / len(needed)


# Sites where people with a problem talk to each other: a sourced complaint there is also a place to reach them.
COMMUNITY_DOMAINS = ("reddit.com", "quora.com", "facebook.com", "stackexchange.com", "stackoverflow.com",
                     "news.ycombinator.com", "indiehackers.com", "producthunt.com", "discord", "forum", "community",
                     "groups.google", "linkedin.com", "medium.com", "tripadvisor", "trustpilot.com", "twitter.com", "x.com")

# Cash per missing skill that is enough to learn it (a course) or buy a little help (a freelancer).
HELP_BUDGET_PER_SKILL = 300.0


def score_variant(v: Variant, findings: list[dict], profile: dict, ledger: EvidenceLedger, scoring: dict,
                  templates: list[str]) -> VariantScore:
    w, cap = scoring["weights"], scoring["caps"]["pain_signals"]
    hours = float(profile.get("hours_per_week") or DEFAULTS["hours_per_week"])
    budget = float(profile.get("budget_usd") if profile.get("budget_usd") is not None else DEFAULTS["budget_usd"])
    skills = profile.get("skills") or []

    pain_items = [i for i in _sourced_web(findings, {"pain", "demand"}, ledger)]
    graded = graded_problem_sources(findings, ledger)
    pain_items = [i for i in pain_items if i.url in graded]           # product listings are not evidence of a problem
    pain_claims = [f["claim"] for f in findings if f["kind"] in {"pain", "demand"} and f.get("label") == "SOURCED"
                   and f.get("source_type") != "listing"]
    urls = set(graded)
    domains = {g["domain"] for g in graded.values()}
    pain = min(sum(g["weight"] for g in graded.values()), cap) / cap
    grades = {}
    for g in graded.values():
        grades[g["type"]] = grades.get(g["type"], 0) + 1

    covered, learnable, missing = match_skills(v.skills_needed, skills)
    coverage = skill_coverage(v.skills_needed, skills)
    budget_ok = v.budget_needed_usd <= budget
    spare = budget - v.budget_needed_usd
    help_affordable = bool(missing) and spare >= HELP_BUDGET_PER_SKILL * len(missing)
    month_hours = hours * 4
    hours_ratio = min(1.0, month_hours / v.est_build_hours) if v.est_build_hours > 0 else 1.0
    skill_part = max(coverage, 0.75) if help_affordable else coverage
    founder_fit = round((skill_part + (1.0 if budget_ok else 0.0) + hours_ratio) / 3, 3)
    skills_ok = coverage >= 0.5 or help_affordable
    fit_pass = budget_ok and v.est_build_hours <= month_hours and skills_ok

    channel_items = _sourced_web(findings, {"channel"}, ledger)
    community = [i for i in pain_items + channel_items + _sourced_web(findings, {"complaint"}, ledger)
                 if any(c in (i.domain or "") for c in COMMUNITY_DOMAINS)]
    # A page already counted as evidence of the problem only counts as a place to reach customers when it is a
    # community (a forum, group, Q&A site): an article about the problem is not where customers gather.
    channel_items = [i for i in channel_items if i.url not in urls] + community
    channels = {i.url for i in channel_items}
    channel_domains = sorted({i.domain for i in channel_items})
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
                "alternatives": alternatives, "channels": len(channels), "skills_covered": covered,
                "skills_learnable": learnable, "skills_missing": missing, "help_affordable": help_affordable,
                "budget_needed": v.budget_needed_usd, "month_hours": month_hours, "channel_sites": channel_domains,
                "problem_sites": sorted(domains), "in_templates": in_templates, "problem_claims": pain_claims[:3], "problem_grades": grades,
                "problem_on_market": sum(1 for g in graded.values() if g["on_market"]),
                "problem_urls": sorted(urls)[:5]},
    )


def score_all(variants: list[Variant], findings: dict, profile: dict, ledger: EvidenceLedger, scoring: dict,
              templates: list[str]) -> list[VariantScore]:
    return [score_variant(v, findings.get(v.id, []), profile, ledger, scoring, templates) for v in variants]
