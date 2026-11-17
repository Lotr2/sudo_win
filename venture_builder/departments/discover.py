"""Phase 1 · Discover: fan-out / fan-in (scatter-gather).

Brief Parser → Liaison intake interview + read-back → Policy Guard → Opportunity Scout →
Market Researcher ∥ Competitor Analyst (evidence into the ledger) → Scorer [code] → Clarifier → Gate 1 →
Gate Reporter → founder decision → Mom Test Writer → interview notes.
"""

from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor

from .. import explain, gates, templates
from ..agents import (brief_parser, competitor_analyst, founder_liaison, gate_reporter, market_researcher,
                      mom_test_writer, opportunity_scout)
from ..clarifier import blocking_questions, contradiction_question, contradicts
from .. import labels
from ..context import END, Ask, Context, Next
from ..schemas import DraftProfile
from ..scoring import DEFAULTS, score_all
from ..tools.research import SearchUnavailable, research
from .common import decision, gate_markdown, present_gate, stop_run

NUMERIC = {"budget_usd", "hours_per_week"}
LIST_FIELDS = {"skills", "values"}
FIT_FIELDS = ["hours_per_week", "budget_usd", "skills"]


def _missing(profile: DraftProfile) -> list[str]:
    d = profile.model_dump()
    gaps = [f for f in ["idea", "skills", "budget_usd", "hours_per_week", "market", "values"]
            if d.get(f) in (None, "", [])]
    gaps += [g for g in profile.gaps if g in d and g not in gaps]
    return gaps


def _apply(ctx: Context, field: str, raw: str, *, correction: bool) -> list[str]:
    """Apply a founder answer to the profile; returns decisions to re-run. Raises on a contradiction."""
    p = ctx.dossier.profile
    value: object = raw
    if field in NUMERIC:
        from ..clarifier import _as_number
        num = _as_number(raw)
        if num is None:
            return []
        value = num
    elif field in LIST_FIELDS:
        value = [s.strip() for s in raw.replace(" and ", ",").split(",") if s.strip()]
    prior = ctx.assumptions.by_field(field)
    if prior and prior.status in {"confirmed", "corrected"} and field in NUMERIC and not correction:
        if contradicts(prior.value, value):
            raise ValueError(contradiction_question(field, prior.value, value))
    setattr(p, field, value)
    if correction or (prior and prior.status == "assumed" and str(prior.value) != str(value)):
        return ctx.assumptions.correct(field, value, answer=raw)
    ctx.assumptions.confirm(field, value, answer=raw)
    return []


# ---------------- intake ----------------

def p1_parse(ctx: Context, answer) -> Next:
    d = ctx.dossier
    ctx.store.converse("founder", d.brief, phase=1, kind="brief")
    hit = ctx.guard.check(d.brief, context="brief", use_classifier=False, subject="brief")
    d.profile = brief_parser.run(ctx, d.brief)
    if not hit.allowed:
        d.policy_block = labels.policy_reason(hit)
        return Next("p1_gate")
    for f in ["idea", "skills", "budget_usd", "hours_per_week", "market", "values"]:
        v = getattr(d.profile, f)
        if v not in (None, "", []):  # stated by the founder in the brief: a founder_reported fact
            ctx.assumptions.confirm(f, v, answer="stated in the brief")
    if not ctx.settings.single_model:
        ctx.say("Hi! Before we start, one privacy note: part of your team runs on Google's free AI service, so what "
                "you type here is sent to Google. Please never share passwords or card numbers. (To keep everything "
                "on this computer instead, the person who set up the app can switch Google off.)", kind="notice")
    return Next("p1_intake_ask")


def p1_intake_ask(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    limit = ctx.t["demo"]["max_intake_questions"] if ctx.demo else 6
    asked = [a.removeprefix("intake:") for a in d.asked if a.startswith("intake:")]
    todo = [f for f in _missing(d.profile) if f not in asked]
    if not todo or len(asked) >= limit:
        return Next("p1_readback")
    field = todo[0]
    q = founder_liaison.intake_question(ctx, d.profile.model_dump(), field, asked)
    d.asked.append(f"intake:{field}")
    ctx.say(f"{q.question}\n\nWhy I'm asking: {q.why_it_matters}", kind="question")
    return Ask("question", {"field": field, "question": q.question, "why": q.why_it_matters},
               resume_to="p1_intake_answer", timeout=ctx.question_timeout())


def p1_intake_answer(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    field = d.asked[-1].removeprefix("intake:")
    if answer is None:
        ctx.say(f"No problem. I'll make a sensible guess about your {labels.field_name(field)} for now and mark it "
                "as a guess (ASSUMPTION). You can correct it any time.")
        return Next("p1_intake_ask")
    text = answer if isinstance(answer, str) else answer.get("text", "")
    ups = founder_liaison.extract_updates(ctx, f"intake question about {field}", text, d.profile.model_dump())
    for up in ups.updates or []:
        if up.field == "other":
            d.founder_inputs.append({"phase": 1, "text": up.value, "type": "founder_reported"})
            continue
        try:
            _apply(ctx, up.field, up.value, correction=False)
        except ValueError as exc:
            d.asked.append(f"contradiction:{up.field}")
            ctx.say(str(exc), kind="question")
            return Ask("question", {"field": up.field, "question": str(exc), "why": "contradiction"},
                       resume_to="p1_contradiction_answer", timeout=ctx.question_timeout())
    return Next("p1_intake_ask")


def p1_contradiction_answer(ctx: Context, answer) -> Next:
    field = ctx.dossier.asked[-1].removeprefix("contradiction:")
    if answer:
        text = answer if isinstance(answer, str) else answer.get("text", "")
        _apply(ctx, field, text, correction=True)
    return Next("p1_intake_ask")


def p1_readback(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    p = d.profile
    for f in _missing(p):
        default = DEFAULTS.get(f)
        if default is not None:
            ctx.assumptions.add(f, default, status="assumed", source="system")
            setattr(p, f, default)
        else:
            ctx.assumptions.add(f, "not given", status="assumed", source="system")
    open_items = [f"Your {labels.field_name(a.field)}: {labels.field_value(a.field, a.value)}" for a in ctx.assumptions.open()]
    rb = founder_liaison.read_back(ctx, p.model_dump(), open_items)
    guesses = rb.open_assumptions or open_items
    ctx.say(f"Here's what I understood:\n\n{rb.summary}"
            + ("\n\nThings I'm guessing for now:\n" + "\n".join(f"- {g}" for g in guesses) if guesses else "")
            + "\n\nDid I get that right? Reply yes, or tell me what to correct.", kind="readback")
    return Ask("readback", {"summary": rb.summary, "open_assumptions": rb.open_assumptions or open_items},
               resume_to="p1_readback_answer", timeout=ctx.question_timeout())


def p1_readback_answer(ctx: Context, answer) -> Next:
    d = ctx.dossier
    if answer is None:
        ctx.say("No reply, so I'll go with my summary. Anything you didn't confirm stays marked as a guess (ASSUMPTION).")
        return Next("p1_policy")
    if isinstance(answer, dict) and answer.get("confirm"):
        for a in list(ctx.assumptions.items.values()):
            if a.source == "founder_reported" and a.status == "assumed":
                ctx.assumptions.confirm(a.field, a.value, answer="confirmed at read-back")
        d.readback_confirmed = True
        return Next("p1_policy")
    text = answer.get("correction", "") if isinstance(answer, dict) else str(answer)
    ups = founder_liaison.extract_updates(ctx, "correction to the read-back", text, d.profile.model_dump())
    for up in ups.updates or []:
        if up.field != "other":
            _apply(ctx, up.field, up.value, correction=True)
    return Next("p1_readback")


def p1_policy(ctx: Context, answer) -> Next:
    d = ctx.dossier
    text = json.dumps(d.profile.model_dump(), ensure_ascii=False)
    hit = ctx.guard.check(text, context="brief", author_family=ctx.author_family("brief_parser"), subject="profile")
    if not hit.allowed:
        d.policy_block = labels.policy_reason(hit)
        return Next("p1_gate")
    return Next("p1_scout")


# ---------------- research ----------------

def p1_scout(ctx: Context, answer) -> Next:
    d = ctx.dossier
    out = opportunity_scout.run(ctx, d.profile.model_dump(), d.lessons, templates.FORMS)
    d.variants = out.variants
    ctx.note(f"Opportunity Scout proposed {len(d.variants)} variants: " + "; ".join(v.name for v in d.variants))
    return Next("p1_research")


def _research_variant(ctx: Context, variant, agent_mod, n_queries: int, max_sources: int) -> list[dict]:
    vd = variant.model_dump()
    plan = agent_mod.plan(ctx, vd, n_queries)
    ids: list[str] = []
    for q in plan.queries[:n_queries]:
        ids += research(q, ledger=ctx.ledger, search=ctx.search, fetch=ctx.fetch, max_sources=max_sources,
                        agent=agent_mod.__name__.rsplit(".", 1)[-1])
        if ctx.over_budget(ctx.t["demo"].get("node_budget_seconds", 240)):
            break
    ids = list(dict.fromkeys(ids))[: max_sources * n_queries]
    if not ids:
        return []
    out = agent_mod.findings(ctx, vd, ctx.ledger.listing(ids))
    labelled = []
    for f in out.findings:
        claim = ctx.ledger.check_claim(f.claim, f.evidence_ids)
        labelled.append({"kind": f.kind, "claim": f.claim, "evidence_ids": f.evidence_ids, "label": claim.label,
                         "sources": claim.sources})
    return labelled


def p1_research(ctx: Context, answer) -> Next:
    d = ctx.dossier
    repeats = d.count("gate1_repeat")
    n_queries = (1 if ctx.demo else 2) + repeats
    max_sources = ctx.t["demo"]["max_sources_per_variant"] if ctx.demo else 6
    jobs = [(v, m) for v in d.variants for m in (market_researcher, competitor_analyst)]
    d.search_failed = False
    results: dict[str, list[dict]] = {v.id: list(d.findings.get(v.id, [])) if repeats else [] for v in d.variants}

    def job(v, m):
        return v.id, _research_variant(ctx, v, m, n_queries, max_sources)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(contextvars.copy_context().run, job, v, m) for v, m in jobs]
        for fut in futures:
            try:
                vid, found = fut.result()
                results[vid] += found
            except SearchUnavailable as exc:
                d.search_failed = True
                ctx.note(f"Search failed: {exc}. Claims fall back to ASSUMPTION; Gate 1 will say repeat.")
    d.findings = results
    return Next("p1_score")


# ---------------- scoring, clarifying questions, gate ----------------

def _gate1_for(ctx: Context, profile: dict):
    scores = score_all(ctx.dossier.variants, ctx.dossier.findings, profile, ctx.ledger, ctx.settings.scoring(),
                       templates.FORMS)
    res = gates.gate1.__wrapped__(scores, excluded=ctx.dossier.policy_block, search_failed=ctx.dossier.search_failed,
                                  repeats=ctx.dossier.count("gate1_repeat"), t=ctx.t)
    top = max(scores, key=lambda s: s.score).variant_id if scores else None
    return top, res.outcome


def p1_score(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    profile = d.profile.model_dump()
    d.scores = score_all(d.variants, d.findings, profile, ctx.ledger, ctx.settings.scoring(), templates.FORMS)
    from ..decision_log import log_action
    log_action("scorer", "score_variants", scores={s.variant_id: s.score for s in d.scores})
    for f in FIT_FIELDS:
        ctx.assumptions.depend(f, "p1_score")
        ctx.assumptions.depend(f, "gate1")
    asked = {a.removeprefix("clarify:") for a in d.asked if a.startswith("clarify:")}
    limit = ctx.blocking_limit()

    def evaluate_with(field, value):
        changed = dict(profile)
        changed[field] = value
        return _gate1_for(ctx, changed)

    qs = [q for q in blocking_questions(ctx.assumptions, {f: profile.get(f) for f in FIT_FIELDS},
                                        evaluate_with, "gate1", limit=3) if q.field not in asked]
    if qs and len(asked) < limit:
        q = qs[0]
        d.asked.append(f"clarify:{q.field}")
        fname = labels.field_name(q.field)
        text = (f"Quick check: your {fname} changes which version of the idea looks best. I'm assuming "
                f"{labels.field_value(q.field, q.current)}. Is that right, or is it closer to "
                f"{labels.field_value(q.field, q.alternatives[0])}?")
        ctx.say(text, kind="question")
        return Ask("question", {"field": q.field, "question": text, "why": q.why, "blocking": True},
                   resume_to="p1_clarify_answer", timeout=ctx.question_timeout())
    return Next("p1_gate")


def p1_clarify_answer(ctx: Context, answer) -> Next:
    d = ctx.dossier
    field = d.asked[-1].removeprefix("clarify:")
    if answer is None:
        ctx.say(f"No answer, so I'll keep my guess about your {labels.field_name(field)}. Because the recommendation "
                "depends on it, you'll be asked to confirm it before going ahead.")
        return Next("p1_gate")
    text = answer if isinstance(answer, str) else answer.get("text", "")
    ups = founder_liaison.extract_updates(ctx, f"question about {field}", text, d.profile.model_dump())
    rerun: list[str] = []
    values = [u for u in ups.updates if u.field == field] or [u for u in ups.updates if u.field != "other"]
    if not values:  # "yes, that's right"
        ctx.assumptions.confirm(field, getattr(d.profile, field), answer=text)
    for up in values:
        prior = getattr(d.profile, up.field)
        rerun += _apply(ctx, up.field, up.value, correction=str(prior) != str(up.value))
    if "p1_score" in rerun:
        ctx.note("Correction affects scoring and Gate 1 only: re-running the scorer, not the research.")
        return Next("p1_score")
    return Next("p1_gate")


def p1_gate(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    res = gates.gate1(d.scores, excluded=d.policy_block, search_failed=d.search_failed,
                      repeats=d.count("gate1_repeat"), t=ctx.t, names={v.id: v.name for v in d.variants})
    explained = explain.explain_all(d.variants, d.scores, ctx.settings.scoring()["weights"], ctx.t["gate1"],
                                    d.profile.model_dump())
    founder = {"original_idea": d.brief, "profile": d.profile.model_dump()}
    memo = gate_reporter.run(ctx, 1, res.model_dump(), {
        "founder": founder, "variants": [v.model_dump() for v in d.variants],
        "score_breakdown": explained}).model_dump()
    d.gate1_explained = {"versions": explained, "directions": memo.get("other_directions", [])}
    _write_phase1_outputs(ctx)
    md = gate_markdown(ctx, res, memo, "Is there a real problem worth solving for this founder?", "gate1",
                       breakdown=explain.markdown(explained))
    options = [{"id": v.id, "name": v.name} for v in d.variants]
    best = explained[0] if explained else None
    return present_gate(ctx, res, md, "p1_gate_decided", {
        "variants": options, "suggested_variant": res.details.get("passing_variant") or res.details.get("top_variant"),
        "versions": explained, "directions": memo.get("other_directions", [])},
        explain={"best_version": best, "other_directions": memo.get("other_directions", [])})


def p1_gate_decided(ctx: Context, answer) -> Next | str:
    d = ctx.dossier
    rec = decision(ctx, 1)
    choice = rec["decision"]
    if choice == "stop":
        return Next(stop_run(ctx, 1, "; ".join(d.gates["1"].reasons)))
    if choice == "repeat":
        d.bump("gate1_repeat")
        return Next("p1_research")
    if choice == "pivot" and not rec["variant_id"]:
        d.lessons.append("Gate 1 pivot: " + "; ".join(d.gates["1"].reasons))
        dirs = (d.gate1_explained or {}).get("directions") or []
        if dirs:
            d.lessons.append("Directions to explore instead: " + "; ".join(
                f"{o['kind']}: {o['suggestion']}" for o in dirs))
        if rec.get("note"):
            d.lessons.append(f"The founder's own note: {rec['note']}")
        d.asked = [a for a in d.asked if not a.startswith("clarify:")]
        return Next("p1_scout")
    d.chosen_variant = rec["variant_id"] or d.gates["1"].details.get("passing_variant") or d.gates["1"].details.get("top_variant")
    return Next("p1_mom_test")


def p1_mom_test(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    variant = next(v for v in d.variants if v.id == d.chosen_variant)
    script = mom_test_writer.run(ctx, variant.model_dump())
    md = [f"# Customer interview script: {variant.name}", "", script.intro, "", "## Questions (about past behaviour)", ""]
    md += [f"{i}. {q}" for i, q in enumerate(script.questions, 1)]
    md += ["", "## Notes template", "", script.notes_template, "",
           "Interviews are run by the founder with real people. Simulated persona interviews, if any, are "
           "labelled SIMULATED and count for nothing at any gate."]
    ctx.store.write_output("phase1_step3_interview_script.md", "\n".join(md) + "\n")
    if ctx.demo:
        d.interview_notes = None
        ctx.note("Demo mode: interview-notes pause skipped; customer-side claims stay ASSUMPTION.")
        return Next("p2_start")
    ctx.say("Next, the most valuable thing you can do: talk to 3 to 5 people who have this problem. I've written a "
            "short interview guide for you (see \"Interview script\" in your documents). Paste your notes here when "
            "you're done. If you'd rather keep going now, just continue: anything about customers will stay marked as "
            "a guess (ASSUMPTION) until you've talked to real people.", kind="question")
    return Ask("notes", {"question": "Paste your interview notes, or continue without them for now.", "optional": True},
               resume_to="p1_notes_answer")


def p1_notes_answer(ctx: Context, answer) -> Next:
    d = ctx.dossier
    text = (answer or {}).get("text", "") if isinstance(answer, dict) else (answer or "")
    if text.strip():
        item = ctx.ledger.add(tool="founder_notes", type="founder_reported", snippet=text[:600])
        d.interview_notes = text
        d.founder_inputs.append({"phase": 1, "type": "founder_reported", "evidence_id": item.id, "text": text[:2000]})
    else:
        d.lessons.append("No customer interviews yet.")
    return Next("p2_start")


KIND_WORDS = {"pain": "People with the problem", "demand": "Signs people want a solution",
              "channel": "Where these customers gather", "alternative": "Existing options",
              "complaint": "Complaints about existing options", "price": "What existing options cost"}


def _source_links(urls: list[str]) -> str:
    from urllib.parse import urlparse
    links = []
    for u in urls[:3]:
        host = urlparse(u).netloc.removeprefix("www.") or u
        links.append(f"[{host}]({u})")
    return ", ".join(links)


def _write_phase1_outputs(ctx: Context) -> None:
    d = ctx.dossier
    explained = {e["id"]: e for e in explain.explain_all(d.variants, d.scores, ctx.settings.scoring()["weights"],
                                                           ctx.t["gate1"], d.profile.model_dump())} if d.scores else {}
    md = ["# Stage 1: Versions of your idea and what the research found", "",
          f"*Your idea:* {d.profile.idea or d.brief.splitlines()[0]}", "",
          "The team turned your idea into a few small, sellable versions, then searched the web for real people "
          "describing the problem, existing competitors, and places where these customers gather. Anything without "
          "a source is marked as a guess (ASSUMPTION).", ""]
    if d.policy_block:
        md += [f"**This idea is outside what the team can help with:** {d.policy_block}", ""]
    if d.search_failed:
        md += ["**Web search wasn't working during this run,** so nothing below could be checked. That says nothing "
               "bad about your idea.", ""]
    for n, v in enumerate(d.variants, 1):
        e = explained.get(v.id)
        md += [f"## {n}. {v.name}" + (f" ({e['total']} out of 100)" if e else ""), "",
               f"- **Who it's for:** {v.target_customer}",
               f"- **Their problem:** {v.problem}",
               f"- **What you'd make:** {explain.FORMS.get(v.product_form, v.product_form)}",
               f"- **Effort:** about {v.est_build_hours:g} hours of work and ${v.budget_needed_usd:,.0f}", ""]
        found = d.findings.get(v.id, [])
        sourced = [f for f in found if f["label"] == "SOURCED"]
        md += ["### What the research found", ""]
        if not sourced:
            md.append("- Nothing with a source yet. This is still a guess (ASSUMPTION).")
        for kind in ("pain", "demand", "channel", "alternative", "complaint", "price"):
            items = [f for f in sourced if f["kind"] == kind]
            if items:
                md.append(f"**{KIND_WORDS[kind]}:**")
                md += [f"- {f['claim']} ({_source_links(f['sources'])})" for f in items]
        guesses = [f for f in found if f["label"] != "SOURCED"]
        if guesses:
            md += ["", "**Still guesses (ASSUMPTION):**"] + [f"- {f['claim']}" for f in guesses]
        if e:
            md += ["", "### Score", ""]
            md += [f"- **{f['name']}: {f['points']} of {f['max']} points.** {f['why']}"
                   + (" Sources: " + _source_links(f["sources"]) + "." if f.get("sources") else "") for f in e["factors"]]
            md += ["", f"**Verdict:** {e['verdict']}"]
        md.append("")
    ctx.store.write_output("phase1_step1_opportunities.md", "\n".join(md))
    weights = ctx.settings.scoring()["weights"]
    ctx.store.write_output("phase1_step2_scores.json", json.dumps(
        {"how_scores_work": "Each version gets up to 100 points: " + ", ".join(
            f"{explain.FACTORS[k].lower()} ({round(w * 100)} points)" for k, w in weights.items())
            + ". These weights are design choices, not facts (config/scoring.toml).",
         "versions": [{"name": e["name"], "total_out_of_100": e["total"], "verdict": e["verdict"],
                       "points": {f["name"]: f"{f['points']} of {f['max']}: {f['why']}" for f in e["factors"]}}
                      for e in sorted(explained.values(), key=lambda x: -x["total"])],
         "raw_scores": [s.model_dump() for s in d.scores]}, indent=1, ensure_ascii=False))


NODES = {
    "p1_parse": p1_parse, "p1_intake_ask": p1_intake_ask, "p1_intake_answer": p1_intake_answer,
    "p1_contradiction_answer": p1_contradiction_answer, "p1_readback": p1_readback,
    "p1_readback_answer": p1_readback_answer, "p1_policy": p1_policy, "p1_scout": p1_scout,
    "p1_research": p1_research, "p1_score": p1_score, "p1_clarify_answer": p1_clarify_answer,
    "p1_gate": p1_gate, "p1_gate_decided": p1_gate_decided, "p1_mom_test": p1_mom_test,
    "p1_notes_answer": p1_notes_answer,
}
_ = END
