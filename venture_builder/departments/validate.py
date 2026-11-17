"""Phase 2 · Validate: generator–critic debate, then a pre-registered experiment.

Liaison kickoff → Canvas Builder (Gemma) ⇄ Skeptical Investor (GPT-OSS, high), with the toughest
objections put to the founder → Experiment Designer → founder approves targets → Threshold Lock (hash in
the log before any result) → Landing Copywriter → Policy Guard → Approval Outbox (publish MOCKED) →
sign-up count (founder-entered or MOCKED) → Gate 2 ★.
"""

from __future__ import annotations

import html
import re

from .. import gates
from ..agents import (canvas_builder, experiment_designer, founder_liaison, gate_reporter, landing_copywriter,
                      skeptical_investor)
from ..clarifier import _as_number
from ..context import Ask, Context, Next
from ..decision_log import log_action
from .common import decision, gate_markdown, items_payload, present_gate, stop_run

BOXES = skeptical_investor.BOXES


def _variant(ctx: Context):
    return next(v for v in ctx.dossier.variants if v.id == ctx.dossier.chosen_variant)


def _evidence_ids(ctx: Context) -> list[str]:
    ids = []
    for f in ctx.dossier.findings.get(ctx.dossier.chosen_variant, []):
        ids += f["evidence_ids"]
    ids += [x["evidence_id"] for x in ctx.dossier.founder_inputs if x.get("evidence_id")]
    return list(dict.fromkeys(i for i in ids if ctx.ledger.get(i)))


def p2_start(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    d.phase = 2
    ctx.note("Phase 2 · Validate started")
    risks = [a.field for a in ctx.assumptions.open()][:3] or ["customers have this problem", "they will pay",
                                                              "we can reach them"]
    msg = founder_liaison.message(ctx, "phase_kickoff", phase="Validate: stress-test the business",
                                  assumptions=risks, variant=_variant(ctx).model_dump())
    ctx.say(msg, kind="kickoff")
    return Ask("question", {"question": msg, "why": "phase kickoff", "optional": True},
               resume_to="p2_kickoff_answer", timeout=ctx.question_timeout())


def p2_kickoff_answer(ctx: Context, answer) -> Next:
    text = answer if isinstance(answer, str) else (answer or {}).get("text") if answer else None
    if text:
        ctx.dossier.founder_inputs.append({"phase": 2, "type": "founder_reported", "text": text})
    return Next("p2_canvas")


def p2_canvas(ctx: Context, answer) -> Next:
    d = ctx.dossier
    payload = {
        "variant": _variant(ctx).model_dump(),
        "evidence": ctx.ledger.listing(_evidence_ids(ctx)),
        "interview_notes": d.interview_notes or "No interviews yet: label every customer-side claim as an assumption.",
        "founder_input": [x["text"] for x in d.founder_inputs if x.get("text")][-5:],
        "objection_answers": d.objection_answers,
        "previous_canvas": d.canvas.model_dump() if d.canvas else None,
        "critique": d.review.model_dump() if d.review else None,
    }
    canvas = canvas_builder.run(ctx, payload)
    for name, box in canvas.model_dump().items():
        if box["evidence_ids"]:
            claim = ctx.ledger.check_claim(box["text"], box["evidence_ids"])
            getattr(canvas, name).assumption = claim.label == "ASSUMPTION"
        else:
            getattr(canvas, name).assumption = True
    d.canvas = canvas
    return Next("p2_review")


def p2_review(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    rnd = d.bump("p2_review_round")
    d.review = skeptical_investor.run(ctx, d.canvas.model_dump(), ctx.ledger.listing(_evidence_ids(ctx)))
    weak = [s for s in d.review.scores if s.score <= 2]
    if rnd == 1 and (d.review.objections or weak):
        objections = (d.review.objections or [f"{s.box}: {s.comment}" for s in weak])[:3]
        text = ("A sceptical reviewer read your one-page business plan. These are the toughest questions an investor "
                "or customer might ask:\n" + "\n".join(f"- {o}" for o in objections) +
                "\n\nHow would you answer them? A sentence or two each is plenty. Your answers help the team improve "
                "the plan (they describe your situation; they don't count as proof that customers want it).")
        ctx.say(text, kind="question")
        return Ask("question", {"question": text, "objections": objections, "why": "investor objections"},
                   resume_to="p2_objection_answer", timeout=ctx.question_timeout())
    if rnd < ctx.t["limits"]["debate_rounds"] and weak:
        return Next("p2_canvas")
    return Next("p2_experiment")


def p2_objection_answer(ctx: Context, answer) -> Next:
    text = answer if isinstance(answer, str) else (answer or {}).get("text") if answer else None
    if text:
        ctx.dossier.objection_answers.append({"type": "founder_reported", "answer": text})
    return Next("p2_canvas")


def p2_experiment(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    floors = ctx.t["gate2"]["floors"]
    risks = sorted((r.model_dump() for r in d.review.risks), key=lambda r: -r["severity"])
    prop = experiment_designer.run(ctx, d.canvas.model_dump(), risks, floors)
    exp = prop.model_dump()
    floor = floors["waitlist_signups"] if prop.kind == "waitlist" else floors["visitor_signup_rate"]
    if prop.target < floor:
        exp["target"] = floor
        exp["raised_to_floor"] = True
    exp["window_days"] = min(prop.window_days, floors["max_window_days"])
    d.experiment = exp
    _write_canvas(ctx)
    item = ctx.outbox.propose(kind="experiment_targets", action="lock",
                              title=f"Demand test goal: {exp['target']:g} {exp['metric']} in {exp['window_days']} days",
                              content={k: exp[k] for k in ("kind", "metric", "target", "window_days")}, phase=2)
    ctx.say(f"Here's a simple test to see if people really want this: {exp['description']}\n\nSuccess means "
            f"{exp['target']:g} {exp['metric']} within {exp['window_days']} days (the lowest sensible target is "
            f"{floor:g}). Setting the goal now, before any results, keeps the test honest. Approve it, or ask for "
            "changes and say what target you'd prefer.", kind="approval")
    return Ask("approval", items_payload(ctx, [item["id"]]), resume_to="p2_lock")


def p2_lock(ctx: Context, answer) -> Next:
    d = ctx.dossier
    item = ctx.outbox.items(phase=2)[-1]
    targets = dict(item["content"])
    if item["status"] == "rejected":
        new = _as_number(item["note"] or "")
        if new:
            floors = ctx.t["gate2"]["floors"]
            floor = floors["waitlist_signups"] if targets["kind"] == "waitlist" else floors["visitor_signup_rate"]
            if new < floor:
                log_action("founder", "override_target_below_floor", approved_by="founder", target=new, floor=floor)
            targets["target"] = new
        ctx.store.converse("founder", f"Target changed: {item['note']}", phase=2)
    elif ctx.outbox.valid_approval(item["id"]) is None:
        raise RuntimeError("targets resumed without an approval or rejection")
    d.locked_targets = targets
    d.lock_hash = gates.lock_hash(targets)
    log_action("threshold_lock", "lock_targets", approved_by="founder", targets=targets, hash=d.lock_hash)
    _write_experiment(ctx)
    return Next("p2_landing")


LANDING_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{headline}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;color:#1b1b1b}}
h1{{font-size:2rem}} li{{margin:.4rem 0}} form{{margin-top:2rem}} input,button{{font-size:1rem;padding:.5rem}}</style>
</head>
<body>
<!-- Waitlist page drafted by the Landing Copywriter. Publishing is MOCKED: the founder hosts it. -->
<h1>{headline}</h1>
<p>{subheadline}</p>
<ul>{benefits}</ul>
<p>{body}</p>
<form onsubmit="return false"><label for="email">Email</label> <input id="email" type="email" required>
<button type="submit">{cta}</button></form>
<p><small>We only use your email to tell you when this is ready. No spam, unsubscribe any time.</small></p>
</body>
</html>
"""


def p2_landing(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    feedback = next((h["landing_feedback"] for h in reversed(d.history) if "landing_feedback" in h), "")
    for attempt in (1, 2):
        copy = landing_copywriter.run(ctx, d.canvas.model_dump(), _variant(ctx).model_dump(), feedback)
        text = " ".join([copy.headline, copy.subheadline, copy.body, copy.cta_text, *copy.benefits])
        hit = ctx.guard.check(text, context="outbox", author_family=ctx.author_family("landing_copywriter"),
                              subject="landing_page")
        if hit.allowed:
            break
        feedback = f"Rejected by the Policy Guard: {hit.reason}. Remove it."
    else:
        raise RuntimeError("landing copy failed the Policy Guard twice")
    page = LANDING_TEMPLATE.format(headline=html.escape(copy.headline), subheadline=html.escape(copy.subheadline),
                                   benefits="".join(f"<li>{html.escape(b)}</li>" for b in copy.benefits),
                                   body=html.escape(copy.body), cta=html.escape(copy.cta_text))
    ctx.store.write_output("phase2_step3_landing_page.html", page)
    item = ctx.outbox.propose(kind="landing_page", action="publish", title="Your waitlist page (publishing MOCKED)",
                              content={"html": page}, phase=2)
    d.landing_item = item["id"]
    ctx.say("Your waitlist page is ready to review. Practice mode: approving it doesn't put it online (MOCKED). "
            "If you want real sign-ups, you can host the page yourself afterwards (it's in your documents).",
            kind="approval")
    return Ask("approval", items_payload(ctx, [item["id"]]), resume_to="p2_publish")


def p2_publish(ctx: Context, answer) -> Next:
    d = ctx.dossier
    item = ctx.outbox.get(d.landing_item)
    if item["status"] == "rejected":
        if d.bump("landing_rewrites") <= 1:
            d.history.append({"event": "landing_rejected", "landing_feedback": item["note"] or ""})
            return Next("p2_landing")
        ctx.say("You asked for changes to the waitlist page twice, so we'll continue without it for now. You can "
                "still share your own page or a simple sign-up form for the demand test.")
    else:
        ctx.outbox.publish(d.landing_item, adapter="MOCKED landing-page host")
    return Next("p2_signups")


def p2_signups(ctx: Context, answer) -> Ask:
    t = ctx.dossier.locked_targets
    ctx.say(f"When your {t['window_days']}-day test is done, enter how many {t['metric']} you got. If you're just "
            "trying the app out, you can enter a practice number: it's labelled MOCKED and never counts as real proof.",
            kind="question")
    return Ask("mocked_input", {"question": f"How many {t['metric']} did you get?", "fields": [
        {"name": "result", "label": f"Number of {t['metric']}", "type": "number"},
        {"name": "mocked", "label": "Is this a practice number (MOCKED) rather than real sign-ups?", "type": "bool",
         "default": True}]},
               resume_to="p2_gate")


def p2_gate(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    if answer is not None:
        d.signups = {"result": float(answer["result"]), "mocked": bool(answer.get("mocked", True))}
        etype = "mocked" if d.signups["mocked"] else "founder_reported"
        ctx.ledger.add(tool="founder_input_form", type=etype,
                       snippet=f"{d.locked_targets['metric']}: {d.signups['result']:g} ({etype})")
        log_action("founder", "enter_experiment_result", approved_by="founder", **d.signups)
    fatal_answered = bool(d.objection_answers)
    res = gates.gate2(d.review, locked=d.locked_targets, recorded_hash=d.lock_hash, result=d.signups["result"],
                      mocked=d.signups["mocked"], repeats=d.count("gate2_repeat"), fatal_answered=fatal_answered,
                      alternative_supported=False, t=ctx.t)
    memo = gate_reporter.run(ctx, 2, res.model_dump() | {"label": res.label},
                             {"experiment": d.experiment, "review": d.review.model_dump()}).model_dump()
    md = gate_markdown(ctx, res, memo, "Would people actually want this?", "gate2")
    if not d.interview_notes:
        md += "\nNo customer interview notes were provided; customer-side claims in the canvas are ASSUMPTIONS.\n"
    return present_gate(ctx, res, md, "p2_gate_decided")


def p2_gate_decided(ctx: Context, answer) -> Next:
    d = ctx.dossier
    rec = decision(ctx, 2)
    if rec["decision"] == "stop":
        return Next(stop_run(ctx, 2, "; ".join(d.gates["2"].reasons)))
    if rec["decision"] == "repeat":
        d.bump("gate2_repeat")
        d.counters["p2_review_round"] = 0
        return Next("p2_canvas")
    if rec["decision"] == "pivot":
        d.lessons.append("Gate 2 pivot: " + "; ".join(d.gates["2"].reasons))
        d.phase = 1
        return Next("p1_scout")
    return Next("p3_start")


def _write_canvas(ctx: Context) -> None:
    d = ctx.dossier
    md = [f"# Phase 2 · Step 1: Lean Canvas ({_variant(ctx).name})", ""]
    for name in BOXES:
        box = getattr(d.canvas, name)
        src = ", ".join(ctx.ledger.urls(box.evidence_ids)) if not box.assumption else "ASSUMPTION"
        md += [f"## {name.replace('_', ' ').title()}", "", box.text, "", f"Source: {src}", ""]
    md += ["## Skeptical Investor review (rubric 1-5)", "", "| Box | Score | Comment |", "|---|---|---|"]
    md += [f"| {s.box} | {s.score} | {s.comment} |" for s in d.review.scores]
    md += ["", "### Riskiest assumptions", ""]
    md += [f"- {r.assumption} (risk {r.severity} of 5{'; could sink the business if wrong' if r.fatal else ''})" for r in
           sorted(d.review.risks, key=lambda r: -r.severity)]
    if d.objection_answers:
        md += ["", "### Your answers to the reviewer's questions", "",
               "*Your own view: it describes your situation, and never counts as proof that customers want this.*", ""]
        md += [f"- {a['answer']}" for a in d.objection_answers]
    ctx.store.write_output("phase2_step1_lean_canvas.md", "\n".join(md) + "\n")


def _write_experiment(ctx: Context) -> None:
    d = ctx.dossier
    e, t = d.experiment, d.locked_targets
    target = f"{t['target']:.0%}" if e.get("kind") == "conversion" or t["target"] < 1 else f"{t['target']:g}"
    floors = ctx.t["gate2"]["floors"]
    md = ["# Phase 2 · Step 2: Demand test and its goal", "", f"**The test:** {e['description']}", "",
          f"**Why this goal:** {e['rationale']}", "", "## Goal set before any result existed", "",
          f"- What is measured: {t['metric']}", f"- Success means: {target}", f"- Time allowed: {t['window_days']} days",
          f"- Fingerprint of this goal: `{d.lock_hash}`. It was written to the decision log when the goal was set, "
          "so anyone can check the goal wasn't changed after the results came in.", "",
          f"Lowest goals the team may propose (config/thresholds.toml): {floors['waitlist_signups']} waitlist sign-ups, "
          f"or {floors['visitor_signup_rate']:.0%} of visitors signing up; at most {floors['max_window_days']} days."]
    if e.get("raised_to_floor"):
        md.append("\nThe proposed target was below the floor and was raised to it.")
    ctx.store.write_output("phase2_step2_experiment.md", "\n".join(md) + "\n")


NODES = {
    "p2_start": p2_start, "p2_kickoff_answer": p2_kickoff_answer, "p2_canvas": p2_canvas, "p2_review": p2_review,
    "p2_objection_answer": p2_objection_answer, "p2_experiment": p2_experiment, "p2_lock": p2_lock,
    "p2_landing": p2_landing, "p2_publish": p2_publish, "p2_signups": p2_signups, "p2_gate": p2_gate,
    "p2_gate_decided": p2_gate_decided,
}
_ = re
