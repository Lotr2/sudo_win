"""Helpers shared by the departments: gate presentation, founder gate decisions, STOP memos."""

from __future__ import annotations

import json

from ..approval import gate_token
from ..context import END, Ask, Context
from ..schemas import GateResult


def gate_markdown(ctx: Context, result: GateResult, memo: dict | None, title: str, decision_key: str) -> str:
    lines = [f"# Phase {result.gate} gate decision: {title}", "",
             f"**Recommendation: {result.label.upper()}**" + ("  (evidence is MOCKED)" if result.mocked_evidence else ""), "",
             "## Why", ""]
    lines += [f"- {r}" for r in result.reasons]
    if memo:
        lines += ["", "## Memo", "", memo.get("summary", ""), "", memo.get("explanation", "")]
        if memo.get("what_would_change_it"):
            lines += ["", "## What evidence would change this recommendation", ""]
            lines += [f"- {w}" for w in memo["what_would_change_it"]]
    if result.details:
        lines += ["", "## Inputs", "", "```json", json.dumps(result.details, indent=1, default=str), "```"]
    lines += ["", ctx.assumptions.rests_on_markdown(decision_key, ctx.brief_fields())]
    lines += ["", "The founder makes the final decision with the gate command or button; an override of this "
              "recommendation is logged against it."]
    return "\n".join(lines) + "\n"


def kickoff(ctx: Context, what: str, assumptions: list[str]) -> None:
    """Phase kickoff from the Founder Liaison (non-blocking; the founder can reply in the chat any time)."""
    from ..agents import founder_liaison
    if ctx.demo:
        ctx.say(f"Next: {what}. It relies on: {'; '.join(assumptions)}.", kind="kickoff")
        return
    ctx.say(founder_liaison.message(ctx, "phase_kickoff", phase=what, assumptions=assumptions), kind="kickoff")


def walkthrough(ctx: Context, result: GateResult) -> None:
    """Gate walk-through by the Founder Liaison: explains the memo; it cannot change the outcome."""
    from ..agents import founder_liaison
    base = f"Gate {result.gate} recommends {result.label.upper()}: {result.reasons[0]}"
    if ctx.demo:
        ctx.say(base, kind="gate")
        return
    rests = [f"{a.field} = {a.value} ({a.status})" for a in ctx.assumptions.rests_on(f"gate{result.gate}")]
    msg = founder_liaison.message(ctx, "gate_walkthrough", gate=result.gate, outcome=result.label,
                                  reasons=result.reasons, rests_on=rests)
    ctx.say(f"{base}\n\n{msg}", kind="gate")


def present_gate(ctx: Context, result: GateResult, markdown: str, resume_to: str, extra: dict | None = None) -> Ask:
    d = ctx.dossier
    n = result.gate
    attempt = d.bump(f"gate{n}_attempt")
    d.gates[str(n)] = result
    crit = [a.field for a in ctx.assumptions.critical_unconfirmed(f"gate{n}")]
    result.critical_unconfirmed = crit
    result.rests_on = [a.id for a in ctx.assumptions.rests_on(f"gate{n}")]
    ctx.store.write_output(f"phase{n}_gate_decision.md", markdown)
    walkthrough(ctx, result)
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
        ctx.say(f"You chose {rec['decision'].upper()} against the recommendation of "
                f"{rec['recommended'].upper()}. What should change? I've noted: {rec['note'] or '(no note)'}")
    return rec


def stop_run(ctx: Context, gate: int, reason: str) -> str:
    d = ctx.dossier
    d.status = "stopped"
    d.history.append({"event": "stop", "gate": gate, "reason": reason})
    ctx.say(f"The run is stopped at Gate {gate}. {reason}", kind="stop")
    return END


def items_payload(ctx: Context, ids: list[str]) -> dict:
    items = []
    for i in ids:
        it = ctx.outbox.get(i)
        items.append({"id": i, "title": it["title"], "kind": it["kind"], "status": it["status"],
                      "hash": it["content_hash"].removeprefix("sha256:")[:12], "content": it["content"]})
    return {"items": items}
