"""Explain a version's Stage 1 score in plain words: where every point came from and what holds it back.

Code, not a model, writes this, so the explanation always matches the numbers the gate used."""

from __future__ import annotations

from . import labels
from .schemas import Variant, VariantScore

FORMS = {
    "guide": "a written guide (an e-book or PDF)",
    "template_pack": "a pack of ready-to-use templates",
    "web_tool": "a small web tool (like a calculator or planner)",
}

FACTORS = {
    "pain": "Evidence that people have this problem",
    "founder_fit": "Fit with your skills, budget and time",
    "reachability": "Places to reach these customers",
    "buildability": "Can be built as a first version",
    "competition_gap": "Room next to existing options",
}


def _sites(names: list[str], n: int = 4) -> str:
    names = [x for x in names if x]
    if not names:
        return ""
    shown = names[:n]
    text = ", ".join(shown[:-1]) + (" and " if len(shown) > 1 else "") + shown[-1]
    return text + (f" and {len(names) - n} more" if len(names) > n else "")


def _quote(text: str) -> str:
    inner = str(text).strip().rstrip(".").replace("\u201c", "'").replace("\u201d", "'").replace('"', "'")
    return f"\u201c{inner}\u201d"


def _list(items: list[str]) -> str:
    return labels.field_value("skills", list(items)) if items else ""


MARKET_STOP = {"people", "from", "high", "income", "low", "middle", "owners", "owner", "customers", "users", "small",
               "business", "businesses", "who", "with", "and", "the", "for", "in", "of", "their", "first", "years",
               "countries", "speaking", "english", "residents", "local", "online", "need", "needing", "want"}


def market_terms(market: str | None) -> set[str]:
    import re
    return {w for w in re.findall(r"[^\W\d_]{4,}", (market or "").lower()) if w not in MARKET_STOP}


def explain_version(v: Variant, s: VariantScore, weights: dict, g1: dict, profile: dict | None = None) -> dict:
    """{name, total, factors: [{name, points, max, why}], blockers: [...], strengths: [...], verdict}."""
    i = s.inputs or {}
    cap = 5
    factors = []

    pts = lambda key, value: round(weights[key] * 100 * value)  # noqa: E731
    mx = lambda key: round(weights[key] * 100)  # noqa: E731

    sites = i.get("problem_sites") or []
    if s.pain_signals:
        claims = i.get("problem_claims") or []
        terms = market_terms((profile or {}).get("market")) | market_terms(v.target_customer)
        local = not terms or any(t in c.lower() for c in claims for t in terms) or \
            any(t in site for site in sites for t in terms)
        why = (f"Found {labels.plural(s.pain_signals, 'real example')} of people describing this problem or "
               "looking for help with it" + (f", on {_sites(sites)}" if sites else "") + f". Full marks need {cap}."
               + (" For example: " + " / ".join(_quote(c) for c in claims[:2]) + "." if claims else "")
               + ("" if local else " Note: none of these examples mention your market specifically, so they show the "
                  "problem exists in general, not yet that it exists where your customers are."))
    else:
        why = ("The research didn't find people describing this problem online yet. That doesn't mean nobody has it: "
               "it may be discussed in places search can't see (private groups, WhatsApp, in person).")
    factors.append({"name": FACTORS["pain"], "points": pts("pain", s.pain), "max": mx("pain"), "why": why})

    covered, learnable, missing = i.get("skills_covered", []), i.get("skills_learnable", []), i.get("skills_missing", [])
    bits = []
    if covered:
        bits.append(f"you already have what it takes for {_list(covered)}")
    if learnable:
        bits.append(f"{_list(learnable)} can be learned quickly")
    if missing:
        bits.append(f"you'd need help with {_list(missing)}"
                    + (" (your budget can cover a course or a freelancer)" if i.get("help_affordable") else ""))
    hours, month = i.get("build_hours", v.est_build_hours), i.get("month_hours")
    money = i.get("budget_needed", v.budget_needed_usd)
    bits.append(f"it needs about {hours:g} hours" + (f" (you have about {month:g} a month)" if month else "")
                + f" and about ${money:,.0f}" + (f" (your budget is ${i['budget_usd']:,.0f})" if i.get("budget_usd") else ""))
    factors.append({"name": FACTORS["founder_fit"], "points": pts("founder_fit", s.founder_fit),
                    "max": mx("founder_fit"), "why": ("; ".join(bits) + ".").capitalize()})

    chans = i.get("channel_sites") or []
    why = (f"These customers can be found on {_sites(chans)}." if chans else
           "No online places where these customers gather were found yet (forums, groups, communities).")
    factors.append({"name": FACTORS["reachability"], "points": pts("reachability", s.reachability),
                    "max": mx("reachability"), "why": why})

    form = FORMS.get(v.product_form, v.product_form)
    if s.buildability >= 1:
        why = f"It's {form}, which the team can build, and it fits in about a month of your time."
    elif s.buildability > 0:
        why = f"It's {form}, which the team can build, but at about {hours:g} hours it's more than a month of your time."
    else:
        why = "The team can't build this kind of product yet."
    factors.append({"name": FACTORS["buildability"], "points": pts("buildability", s.buildability),
                    "max": mx("buildability"), "why": why})

    comp, alts = i.get("complaints", 0), i.get("alternatives", 0)
    if alts or comp:
        why = (f"Found {labels.plural(alts, 'existing option')}" + (f" and {labels.plural(comp, 'complaint')} about them"
               if comp else " but no complaints about them") + ". Complaints show where you could do better.")
    else:
        why = "No competitors or complaints about them were found, so it's unclear how crowded this space is."
    factors.append({"name": FACTORS["competition_gap"], "points": pts("competition_gap", s.competition_gap),
                    "max": mx("competition_gap"), "why": why})

    blockers = []
    if s.pain_signals < g1["min_pain_signals"]:
        blockers.append(f"needs at least {g1['min_pain_signals']} real examples of the problem (has {s.pain_signals})")
    elif s.pain_domains < g1["min_source_domains"]:
        blockers.append(f"examples must come from at least {g1['min_source_domains']} different websites "
                        f"(they come from {s.pain_domains})")
    if not s.fit_rules_pass:
        if not i.get("budget_ok", True):
            blockers.append(f"needs about ${money:,.0f}, more than your budget")
        if month and hours > month:
            blockers.append(f"needs about {hours:g} hours, more than a month of your time ({month:g} hours)")
        if (missing or learnable) and not i.get("help_affordable") and i.get("skill_coverage", 1) < 0.5:
            blockers.append(f"needs skills you don't list yet: {_list(missing or learnable)}")
    if s.score < g1["min_score"]:
        blockers.append(f"scores {s.score * 100:.0f} out of 100; {g1['min_score'] * 100:.0f} is needed")
    strengths = [f["name"].lower() for f in factors if f["max"] and f["points"] >= 0.8 * f["max"]]
    verdict = ("Passes every check." if not blockers else "Held back because it " + "; it ".join(blockers) + ".")
    return {"id": v.id, "name": v.name, "customer": v.target_customer, "problem": v.problem, "form": form,
            "total": round(s.score * 100), "factors": factors, "blockers": blockers, "strengths": strengths,
            "verdict": verdict}


def explain_all(variants: list[Variant], scores: list[VariantScore], weights: dict, g1: dict,
                profile: dict | None = None) -> list[dict]:
    by_id = {s.variant_id: s for s in scores}
    out = [explain_version(v, by_id[v.id], weights, g1, profile) for v in variants if v.id in by_id]
    return sorted(out, key=lambda e: e["total"], reverse=True)


def markdown(explained: list[dict], heading: str = "## How each version scored") -> list[str]:
    lines = [heading, "",
             "Each version gets up to 100 points from five things. 60 points, plus enough real evidence and a good "
             "fit with you, are needed to go ahead.", ""]
    for e in explained:
        lines += [f"### {e['name']}: {e['total']} out of 100", "",
                  f"*For {e['customer']}. {e['form'].capitalize()}.*", ""]
        for f in e["factors"]:
            lines.append(f"- **{f['name']}: {f['points']} of {f['max']} points.** {f['why']}")
        lines += ["", f"**Verdict:** {e['verdict']}", ""]
    return lines
