"""Simulated founder for the committed demo run (MOCKED founder decisions).

Runs a brief through the real pipeline (real models, real search) and answers every founder pause with fixed
rules, so one run can show all six stages. Every decision it makes goes through the normal approval code on the
separate "simulation" channel and is labelled `simulated_founder: true` in the decision log. The web UI refuses
to start while VENTURE_SIMULATED_FOUNDER=1 is set, so real use only ever accepts approvals from a person.

Rules:
- Read-back and clarifying checks ("is your budget X?"): confirms them, as the brief states these facts.
- Questions the founder answers from their own situation (the reviewer's tough questions, pricing
  preferences, launch channels): answers from FOUNDER_ANSWERS below, written from the brief. Founder answers
  are founder_reported and never count as market evidence.
- Optional check-ins ("anything to add?"): nothing to add, the same as pressing Continue.
- Customer interview notes and any other question: skipped. Interviews are never invented, so customer-side
  claims stay labelled ASSUMPTION.
- Approvals (pages, posts, targets, refunds, support replies): approves each item.
- Stage checks (`--follow`): takes the team's recommendation, whatever it is (used for the weak idea).
- Stage checks (default, "to the end"): takes "go" when recommended; takes "repeat" once per stage when
  recommended; otherwise continues with "go" as a logged override, with the reason in the note, so the demo
  shows every stage. Unconfirmed decision-critical guesses are confirmed first, as the app requires.
- Stage checks (`--follow-from N`): the default rules before stage N, the team's recommendation from N on
  (used for the demand-test STOP run, so a Stage 2 STOP is followed, not overridden).
- Practice numbers (always labelled MOCKED): the demand test reports exactly its locked target, or the number
  given with `--demand-result`; the review period reports 400 visitors, 14 orders, 1 refund and two support
  questions.

Usage:
    python scripts/simulate_founder.py briefs/plausible_client_kit.md          # all six stages
    python scripts/simulate_founder.py briefs/weak_meal_coach.md --follow      # follows the STOP
    python scripts/simulate_founder.py briefs/plausible_client_kit.md --demand-result 12 --follow-from 2
                                                    # practice demand test far below its target: Stage 2 STOP
    venture export RUN_ID; venture export DEMAND_STOP_RUN_ID --tag demandstop --append;
    venture export EXCLUDED_RUN_ID --tag excluded --append                   # commit the demo run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ["VENTURE_SIMULATED_FOUNDER"] = "1"
os.environ.setdefault("RUN_MODE", "demo")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from venture_builder.approval import SIMULATION_CHANNEL, FounderConfirmation  # noqa: E402
from venture_builder.config import load_settings  # noqa: E402
from venture_builder.runtime import Runtime  # noqa: E402

TICKETS = "How do I download the templates after I buy?\nCan I get a refund if the kit doesn't suit my workflow?"

# Answers the founder of briefs/plausible_client_kit.md would give, taken from that brief (8 years of branding
# work for small businesses, clear client emails, honest pricing, no spammy marketing, $150, 6 hours a week).
FOUNDER_ANSWERS = {
    "investor objections": (
        "My first buyers come from people I already know: designers in my network and the online groups I'm "
        "part of, where late payment comes up often. I'd keep the first version small and cheap to change, price "
        "it honestly (one price, no upsell pressure) and only add more once people actually buy it. It costs me "
        "about $150 and 6 hours a week, so I can afford to test it before investing more."),
    "pricing and refund preferences": (
        "Keep it simple and honest: one price that a new freelancer can afford, no countdowns, and refunds within "
        "14 days if it doesn't help."),
    "channels and tone": "LinkedIn, freelance designer groups I belong to, my email newsletter",
}


def confirm_item(rt, item_id: str) -> FounderConfirmation:
    h = rt.ctx.outbox.get(item_id)["content_hash"].removeprefix("sha256:")
    return FounderConfirmation(channel=SIMULATION_CHANNEL, item_ref=item_id, typed_hash_prefix=h[:8])


def confirm_gate(p: dict) -> FounderConfirmation:
    return FounderConfirmation(channel=SIMULATION_CHANNEL, item_ref=f"gate{p['gate']}-{p['attempt']}",
                               typed_hash_prefix=p["code"])


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def decide(rt, p: dict, follow: bool, repeats: dict, follow_from: int | None = None) -> None:
    rec, gate = p["recommended"], p["gate"]
    if follow or (follow_from is not None and gate >= follow_from):
        choice = rec
    elif rec == "go":
        choice = "go"
    elif rec == "repeat" and repeats.get(gate, 0) < 1:
        choice = "repeat"
        repeats[gate] = repeats.get(gate, 0) + 1
    else:
        choice = "go"
    if p.get("allowed") and choice not in p["allowed"]:
        choice = rec            # e.g. a failed step offers only repeat or stop
    note = ""
    if choice != rec:
        note = (f"Simulated founder for the demo run: continues so the run shows all six stages "
                f"(the team recommended {rec}).")
    if choice == "go":
        for field in p.get("critical_unconfirmed") or []:
            rt.confirm_assumption(field)
            say(f"  confirmed the guess about {field}")
    variant = p.get("suggested_variant") if gate == 1 and choice in {"go", "pivot"} else None
    say(f"Stage {gate} check: team recommends {rec.upper()}; simulated founder chooses {choice.upper()}"
        + (" (override)" if note else ""))
    rt.decide_gate(choice, confirm_gate(p), variant_id=variant, note=note)


def fill_form(rt, p: dict, demand_result: float | None = None) -> None:
    locked = rt.dossier.locked_targets or {}
    values = {}
    for f in p["fields"]:
        name = f["name"]
        if f["type"] == "bool":
            values[name] = True                      # practice numbers: MOCKED
        elif name == "result":
            values[name] = float(demand_result if demand_result is not None else (locked.get("target") or 30))
        elif name == "signups":     # a rate target: sign-ups out of 400 practice visitors
            values[name] = float(demand_result if demand_result is not None else round(400 * float(locked.get("target") or 0.05)))
        elif name == "tickets" or f["type"] == "text":
            values[name] = TICKETS
        else:
            values[name] = {"visitors": 400, "orders": 14, "refunds": 1}.get(name, 0)
    say(f"Practice numbers (MOCKED): {values}")
    rt.submit_input(values)


def run(brief_path: str, follow: bool, max_steps: int = 400, follow_from: int | None = None,
        demand_result: float | None = None) -> Runtime:
    settings = load_settings(dict(os.environ))
    rt = Runtime.new(settings, Path(brief_path).read_text(), data_mode="practice")   # a script's numbers are MOCKED
    say(f"Run {rt.run_id} started ({settings.run_mode} mode) from {brief_path}")
    repeats: dict[int, int] = {}
    p = rt.advance()
    last_phase = 0
    for _ in range(max_steps):
        if p is None:
            break
        if rt.dossier.phase != last_phase:
            last_phase = rt.dossier.phase
            say(f"== Stage {last_phase}")
        k = p["kind"]
        if k == "readback":
            say("Read-back: confirmed")
            rt.reply("yes")
        elif k == "question" and p.get("why") in FOUNDER_ANSWERS:
            say(f"Answered ({p['why']}) from the brief")
            rt.reply(FOUNDER_ANSWERS[p["why"]])
        elif k == "question" and p.get("blocking"):
            say(f"Clarifying check about {p.get('field')}: confirmed")
            rt.reply("Yes, that's right.")
        elif k == "question" and p.get("optional"):
            say(f"Check-in ({p.get('why')}): nothing to add")
            rt.skip()
        elif k in {"question", "notes"}:
            say(f"{'Interview notes' if k == 'notes' else 'Question'} skipped (stays a labelled guess)")
            rt.skip()
        elif k == "approval":
            for it in p["items"]:
                if rt.ctx.outbox.get(it["id"])["status"] == "pending":
                    say(f"Approved: {it['title']}")
                    rt.approve(it["id"], confirm_item(rt, it["id"]))
        elif k == "gate":
            decide(rt, p, follow, repeats, follow_from)
        elif k == "mocked_input":
            fill_form(rt, p, demand_result)
        else:
            raise RuntimeError(f"unknown pause: {k}")
        p = rt.pending()
    else:
        raise RuntimeError(f"run {rt.run_id} did not finish within {max_steps} steps")
    say(f"Run {rt.run_id} ended: stage {rt.dossier.phase}, status {rt.status()['status']}")
    say("Outputs: " + ", ".join(sorted(x.name for x in rt.ctx.store.outputs.iterdir())))
    print(f"RUN_ID {rt.run_id}")
    return rt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("brief")
    ap.add_argument("--follow", action="store_true", help="take every recommendation, including stop")
    ap.add_argument("--follow-from", type=int, help="take the team's recommendation from this stage check on")
    ap.add_argument("--demand-result", type=float, help="practice (MOCKED) demand-test result to enter")
    args = ap.parse_args()
    run(args.brief, args.follow, follow_from=args.follow_from, demand_result=args.demand_result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
