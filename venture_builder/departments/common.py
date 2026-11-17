"""Helpers shared by the departments: gate presentation, founder gate decisions, STOP memos."""

from __future__ import annotations

import json

from .. import labels
from ..approval import gate_token
from ..context import END, Ask, Context
from ..schemas import GateResult


STAGE_QUESTIONS = {
    1: "Is there a real problem worth solving, and does it fit you?",
    2: "Do enough people want this?",
    3: "Is the product ready and does it work?",
    4: "Are the price and policies sound?",
    5: "Is the launch ready?",
    6: "Should you keep going or change course?",
}


def _readable_details(details: dict, depth: int = 0) -> list[str]:
    """Gate inputs as a readable list (never raw JSON)."""
    lines = []
    pad = "  " * depth
    for k, v in (details or {}).items():
        if k in {"top_variant", "passing_variant"}:  # internal ids; the names are shown instead
            continue
        name = labels.detail_name(k)
        v = labels.detail_value(k, v)
        if isinstance(v, dict):
            lines.append(f"{pad}- {name}:")
            lines += _readable_details(v, depth + 1)
        elif isinstance(v, (list, tuple)):
            shown = ", ".join(str(x) for x in v) if v else "none"
            lines.append(f"{pad}- {name}: {shown}")
        elif isinstance(v, bool):
            lines.append(f"{pad}- {name}: {'yes' if v else 'no'}")
        elif isinstance(v, float):
            lines.append(f"{pad}- {name}: {v:.2f}".rstrip("0").rstrip("."))
        else:
            lines.append(f"{pad}- {name}: {v if v is not None else 'not available'}")
    return lines


def gate_markdown(ctx: Context, result: GateResult, memo: dict | None, title: str, decision_key: str,
                  breakdown: list[str] | None = None) -> str:
    n = result.gate
    lines = [f"# Stage {n} check: {labels.phase_name(n)}", "",
             f"*{STAGE_QUESTIONS.get(n, title)}*", "",
             f"**The team recommends: {labels.outcome_name(result.label, n)}** ({result.label.upper()})"
             + ("  \nThe numbers here are practice data (MOCKED), so they don't count as real validation."
                if result.mocked_evidence else ""), ""]
    if memo and memo.get("summary"):
        lines += ["## In short", "", memo["summary"], ""]
    lines += ["## Why", ""] + [f"- {r}" for r in result.reasons]
    if memo and memo.get("explanation"):
        lines += ["", "## What the team found", "", memo["explanation"]]
    if memo and memo.get("what_would_change_it"):
        lines += ["", "## What you can do next", ""]
        lines += [f"- {w}" for w in memo["what_would_change_it"]]
    if memo and memo.get("other_directions"):
        lines += ["", "## Other directions worth considering", "",
                  "If this version doesn't work out, these grow out of your original idea and skills. They are "
                  "suggestions to explore, not researched findings (ASSUMPTION) until the team checks them:", ""]
        for o in memo["other_directions"]:
            lines.append(f"- **{o['kind'].capitalize()}:** {o['suggestion']} *{o['why']}*")
    if breakdown:
        lines += [""] + breakdown
    if result.details:
        lines += ["", "## Key numbers", ""] + _readable_details(result.details)
    lines += ["", ctx.assumptions.rests_on_markdown(decision_key, ctx.brief_fields())]
    lines += ["", "You make the final call, using the options on screen (or `venture gate` in the terminal). "
              "If you choose differently from the recommendation, your choice and reason are recorded next to it."]
    return "\n".join(lines) + "\n"


def kickoff(ctx: Context, what: str, assumptions: list[str]) -> None:
    """Phase kickoff from the Founder Liaison (non-blocking; the founder can reply in the chat any time)."""
    from ..agents import founder_liaison
    if ctx.demo:
        ctx.say(f"Next up: {what}." + (f" This relies on: {'; '.join(assumptions)}." if assumptions else ""), kind="kickoff")
        return
    ctx.say(founder_liaison.message(ctx, "phase_kickoff", phase=what, assumptions=assumptions), kind="kickoff")


def walkthrough(ctx: Context, result: GateResult, explain: dict | None = None) -> None:
    """Gate walk-through by the Founder Liaison: explains the memo; it cannot change the outcome."""
    from ..agents import founder_liaison
    base = (f"Stage {result.gate} check: the team recommends \"{labels.outcome_name(result.label, result.gate)}\". "
            f"{result.reasons[0]}")
    if ctx.demo:
        ctx.say(base, kind="gate")
        return
    status = {"assumed": "a guess, not confirmed", "confirmed": "confirmed by the founder",
              "corrected": "corrected by the founder"}
    rests = [f"Their {labels.field_name(a.field)}: {labels.field_value(a.field, a.value)} ({status[a.status]})"
             for a in ctx.assumptions.rests_on(f"gate{result.gate}")]
    msg = founder_liaison.message(ctx, "gate_walkthrough", stage=result.gate, stage_name=labels.phase_name(result.gate),
                                  recommendation=labels.outcome_name(result.label, result.gate),
                                  reasons=result.reasons, based_on=rests, **(explain or {}))
    ctx.say(f"Stage {result.gate} check: the team recommends \"{labels.outcome_name(result.label, result.gate)}\".\n\n{msg}",
           kind="gate")


def present_gate(ctx: Context, result: GateResult, markdown: str, resume_to: str, extra: dict | None = None,
                 explain: dict | None = None) -> Ask:
    d = ctx.dossier
    n = result.gate
    attempt = d.bump(f"gate{n}_attempt")
    d.gates[str(n)] = result
    crit = [a.field for a in ctx.assumptions.critical_unconfirmed(f"gate{n}")]
    result.critical_unconfirmed = crit
    result.rests_on = [a.id for a in ctx.assumptions.rests_on(f"gate{n}")]
    ctx.store.write_output(f"phase{n}_gate_decision.md", markdown)
    walkthrough(ctx, result, explain)
    return Ask("gate", {"gate": n, "attempt": attempt, "recommended": result.outcome, "label": result.label,
                        "reasons": result.reasons, "memo_file": f"phase{n}_gate_decision.md",
                        "critical_unconfirmed": crit, "code": gate_token(n, attempt)[:6], **(extra or {})},
               resume_to=resume_to)


def decision(ctx: Context, gate: int) -> dict:
    attempt = ctx.dossier.count(f"gate{gate}_attempt")
    rec = ctx.outbox.gate_decision(gate, attempt)
    if rec is None:
        raise RuntimeError(f"gate {gate} resumed without a recorded founder decision")
    if rec["override"]:
        ctx.say(f"Got it: you chose \"{labels.outcome_name(rec['decision'], gate)}\" instead of the recommended "
                f"\"{labels.outcome_name(rec['recommended'], gate)}\". That's your call. I've noted your reason: "
                f"{rec['note'] or '(none given)'}")
    return rec


def stop_run(ctx: Context, gate: int, reason: str) -> str:
    d = ctx.dossier
    d.status = "stopped"
    d.history.append({"event": "stop", "gate": gate, "reason": reason})
    ctx.say(f"The project is stopped at stage {gate} ({labels.phase_name(gate)}). {reason}\n\nStopping early on weak "
            "evidence is a good outcome: it saves you time and money. You can start a new project any time, for "
            "example with a narrower customer group or a different version of the idea.", kind="stop")
    return END


def items_payload(ctx: Context, ids: list[str]) -> dict:
    items = []
    for i in ids:
        it = ctx.outbox.get(i)
        items.append({"id": i, "title": it["title"], "kind": it["kind"], "status": it["status"],
                      "hash": it["content_hash"].removeprefix("sha256:")[:12], "content": it["content"]})
    return {"items": items}
