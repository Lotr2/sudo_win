"""Phase 6 · Operate and learn: monitor loop (MAPE-K) with a critic. The event log is MOCKED.

Targets locked and hashed → founder enters MOCKED activity → Metrics Engine [code] → Support Agent
(answers from the user docs only; replies and refunds go to the outbox) → Pivot Memo Writer ⇄ Critic →
Gate 6 → founder: persevere / pivot / stop.
"""

from __future__ import annotations

import re

from .. import gates, metrics
from ..agents import critic, pivot_memo_writer, support_agent
from .. import labels
from ..context import END, Ask, Context, Next
from ..decision_log import log_action
from ..events import EventStore
from ..refunds import request_refund
from .common import decision, gate_markdown, items_payload, present_gate, stop_run


def _events(ctx: Context) -> EventStore:
    return EventStore(ctx.store.path("events.db"))


def p6_start(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    d.phase = 6
    from .common import kickoff
    kickoff(ctx, "Operate and learn: compute metrics from the event log, answer support from the docs, "
                 "write a persevere-or-pivot memo checked by a critic",
            ["the activity entered is MOCKED", "targets are locked before the data"])
    d.gate6_targets = dict(ctx.t["gate6"])
    d.gate6_hash = gates.lock_hash(d.gate6_targets)
    log_action("threshold_lock", "lock_gate6_targets", targets=d.gate6_targets, hash=d.gate6_hash)
    ctx.say(f"Time to review how things went. The goals were set before any data: at least "
            f"{d.gate6_targets['min_conversion']:.0%} of visitors buy, and at most "
            f"{d.gate6_targets['max_refund_rate']:.0%} of buyers ask for a refund. Enter the numbers for the period "
            "(in this version they're practice numbers, labelled MOCKED), plus a few questions buyers asked, one "
            "per line.", kind="question")
    return Ask("mocked_input", {"question": "How did the launch go? (practice numbers, MOCKED)", "fields": [
        {"name": "visitors", "label": "Store visitors (MOCKED)", "type": "number"},
        {"name": "orders", "label": "Orders (MOCKED)", "type": "number"},
        {"name": "refunds", "label": "Refunds (MOCKED)", "type": "number"},
        {"name": "tickets", "label": "Support questions, one per line (MOCKED)", "type": "text"}]},
               resume_to="p6_events")


def p6_events(ctx: Context, answer) -> Next:
    d = ctx.dossier
    store = _events(ctx)
    for k in ("visitors", "orders", "refunds"):
        store.add_activity(k, float(answer.get(k) or 0))
    tickets = [t.strip() for t in str(answer.get("tickets") or "").splitlines() if t.strip()][:5]
    for t in tickets:
        store.add_activity("ticket", 1, t)
    log_action("founder", "enter_mocked_activity", approved_by="founder", label="MOCKED",
               visitors=answer.get("visitors"), orders=answer.get("orders"), refunds=answer.get("refunds"))
    d.metrics = metrics.compute(store)
    t = d.gate6_targets
    md = ["# Phase 6 · Step 1: Results (practice data, MOCKED)", "", "Calculated by code from the activity entered for "
          "the review period. Every number below is practice data (MOCKED), not real sales.", "",
          "| Measure | Value | Goal |", "|---|---|---|",
          f"| Visitors | {d.metrics['visitors']} | |", f"| Orders | {d.metrics['orders']} | |",
          f"| Visitors who bought | {d.metrics['conversion']:.1%} | at least {t['min_conversion']:.0%} |",
          f"| Refunds | {d.metrics['refunds']} | |",
          f"| Buyers who asked for a refund | {d.metrics['refund_rate']:.1%} | at most {t['max_refund_rate']:.0%} |",
          f"| Support tickets | {d.metrics['support_tickets']} | |", "",
          f"Numbers the business plan said to watch: {d.canvas.key_metrics.text if d.canvas else 'not set'}", "",
          f"Fingerprint of the goals: `{d.gate6_hash}`. It was logged before any data was entered, so the goals "
          "can't quietly change afterwards."]
    ctx.store.write_output("phase6_step1_metrics.md", "\n".join(md) + "\n")
    return Next("p6_support")


def _passages(ctx: Context) -> list[dict]:
    docs = ctx.dossier.docs
    paras = [p.strip() for p in docs.user_guide.split("\n\n") if len(p.strip()) > 30]
    paras += [f"Q: {f.q} A: {f.a}" for f in docs.faq]
    paras.append(f"Refund policy: {ctx.dossier.policies.refunds[:600]}")
    return [{"id": f"doc_{i}", "text": p[:700]} for i, p in enumerate(paras, 1)]


def _retrieve(passages: list[dict], query: str, k: int = 4) -> list[dict]:
    terms = set(re.findall(r"[a-z]{4,}", query.lower()))
    scored = sorted(passages, key=lambda p: -len(terms & set(re.findall(r"[a-z]{4,}", p["text"].lower()))))
    return scored[:k]


def p6_support(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    store = _events(ctx)
    tickets = [r["text"] for r in store.rows("SELECT text FROM activity WHERE kind='ticket' ORDER BY id")]
    passages = _passages(ctx)
    ids = []
    d.support = []
    for t in tickets:
        hit = ctx.guard.check(t, context="chat", use_classifier=False, subject="support_ticket")
        reply = support_agent.run(ctx, t, _retrieve(passages, t))
        if reply.is_refund_request or re.search(r"\brefund", t, re.I):
            orders = store.rows("SELECT order_id FROM orders WHERE status='paid' ORDER BY order_id")
            if orders:
                item = request_refund(ctx.outbox, orders[0]["order_id"], f"Support ticket: {t[:120]}", phase=6)
                ids.append(item["id"])
        if not reply.cited_doc_ids:
            reply.escalate = True
        rhit = ctx.guard.check(reply.answer, context="outbox", author_family=ctx.author_family("support_agent"),
                               subject="support_reply")
        if rhit.allowed:
            item = ctx.outbox.propose(kind="support_reply", action="send", title=f"Reply to: {t[:60]} (send MOCKED)",
                                      content={"ticket": t, "reply": reply.answer, "cited": reply.cited_doc_ids},
                                      phase=6)
            ids.append(item["id"])
        d.support.append({"ticket": t, "reply": reply.model_dump(), "guard_ticket": hit.allowed,
                          "guard_reply": rhit.allowed})
    d.history.append({"event": "support_items", "ids": ids})
    if ids:
        ctx.say(f"{len(ids)} support item(s) need your approval: replies are sent only after approval (MOCKED), "
                "and refunds always come to you.", kind="approval")
        return Ask("approval", items_payload(ctx, ids), resume_to="p6_support_send")
    return Next("p6_support_send")


def p6_support_send(ctx: Context, answer) -> Next:
    d = ctx.dossier
    ids = next((h["ids"] for h in reversed(d.history) if h.get("event") == "support_items"), [])
    md = ["# Phase 6 · Step 2: Support log (questions and sending MOCKED)", "",
          "Replies use only the product's own user guide and FAQ, so they never invent promises. Refund requests "
          "always go to the founder for approval.", ""]
    for s in d.support:
        r = s["reply"]
        n = len(r["cited_doc_ids"])
        md += [f"## Question: {s['ticket']}", "", r["answer"], "",
               (f"Answered from {labels.plural(n, 'passage')} of the user guide and FAQ." if n else
                "Not covered by the user guide, so it was passed to the founder."), ""]
    md += ["## What was sent", ""]
    for i in ids:
        item = ctx.outbox.get(i)
        if item["status"] == "approved":
            if item["kind"] == "refund":
                from ..refunds import execute_refund
                execute_refund(ctx.outbox, _events(ctx), i)
            else:
                ctx.outbox.publish(i, adapter="MOCKED email sender")
            item = ctx.outbox.get(i)
        status = {"published": "approved and recorded (sending MOCKED)", "approved": "approved",
                  "rejected": "changes requested", "refunded": "refund approved and recorded (MOCKED)",
                  "pending": "waiting for the founder"}.get(item["status"], item["status"])
        md.append(f"- {item['title']}: **{status}**" + (f" (note: {item['note']})" if item["note"] else ""))
    ctx.store.write_output("phase6_step2_support_log.md", "\n".join(md) + "\n")
    return Next("p6_memo")


def p6_memo(ctx: Context, answer) -> Next:
    d = ctx.dossier
    m = {k: v for k, v in d.metrics.items() if k != "label"}
    feedback: list[str] = []
    review = None
    memo = None
    for _ in range(ctx.t["limits"]["memo_revisions"] + 1):
        memo = pivot_memo_writer.run(ctx, {"metrics": m, "targets": d.gate6_targets, "label": "MOCKED data",
                                           "ticket_themes": [s["ticket"] for s in d.support],
                                           "canvas_key_metrics": d.canvas.key_metrics.text, "critic_gaps": feedback})
        review = critic.run(ctx, memo.model_dump(), m, d.gate6_targets, "MOCKED (practice data entered by the founder)")
        if review.rubric_score >= 3 and all(c.matches_data for c in review.checks):
            break
        feedback = review.gaps + [c.comment for c in review.checks if not c.matches_data]
    passes = review.rubric_score >= 3 and all(c.matches_data for c in review.checks)
    d.memo = {"memo": memo.model_dump(), "review": review.model_dump(), "passes": passes}
    rec = {"persevere": "Keep going", "pivot": "Change direction", "stop": "Stop here"}.get(memo.recommendation,
                                                                                            memo.recommendation)
    md = ["# Phase 6 · Step 3: Keep going or change course? (practice data, MOCKED)", "",
          f"**The review recommends: {rec}**", "", memo.body, "",
          "## Fact-check", "", "A second model checked every statement against the numbers and the goals:", ""]
    for i, c in enumerate(memo.claims):
        chk = next((x for x in review.checks if x.claim_index == i), None)
        ok = bool(chk and chk.matches_data)
        md.append(f"- {'Checks out' if ok else 'Does not check out'}: {c.text}"
                  + ("" if ok or not chk or not chk.comment else f" ({chk.comment})"))
    md += ["", f"Fact-check score: {review.rubric_score} out of 5. "
               + ("The review passes." if passes else "The review does not pass yet, so it is rewritten before the decision.")]
    ctx.store.write_output("phase6_step3_pivot_memo.md", "\n".join(md) + "\n")
    return Next("p6_gate")


def p6_gate(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    if gates.lock_hash(d.gate6_targets) != d.gate6_hash:
        raise gates.ThresholdTamperError("Gate 6 targets changed after locking")
    res = gates.gate6(metrics=d.metrics, targets=d.gate6_targets, memo_passes=d.memo["passes"],
                      alternative_supported=bool(d.lessons), t=ctx.t)
    memo = {"summary": f"The team recommends: {labels.outcome_name(res.label, 6)}.",
            "explanation": "This check compares the review period's results with the goals set before any data "
                           "existed, and only counts the written review if a second model's fact-check passes. The "
                           "numbers are practice data (MOCKED), so this shows how the review works; it is not real "
                           "proof that the product sells.",
            "what_would_change_it": ["Run the product with real visitors and buyers, then enter the real numbers",
                                     "Collect real support questions to see where buyers get stuck"]}
    md = gate_markdown(ctx, res, memo, "Is it working, and what next?", "gate6")
    return present_gate(ctx, res, md, "p6_gate_decided", {"labels": {"go": "persevere"}})


def p6_gate_decided(ctx: Context, answer) -> Next:
    d = ctx.dossier
    rec = decision(ctx, 6)
    if rec["decision"] == "stop":
        return Next(stop_run(ctx, 6, "; ".join(d.gates["6"].reasons)))
    if rec["decision"] == "repeat":
        d.bump("gate6_repeat")
        return Next("p6_memo")
    if rec["decision"] == "pivot":
        d.lessons.append("Gate 6 pivot: " + "; ".join(d.gates["6"].reasons))
        d.phase = 1
        return Next("p1_scout")
    d.status = "finished"
    ctx.say("You chose to persevere. The run is complete; every phase's outputs are in the outputs folder.")
    return Next(END)


NODES = {"p6_start": p6_start, "p6_events": p6_events, "p6_support": p6_support, "p6_support_send": p6_support_send,
         "p6_memo": p6_memo, "p6_gate": p6_gate, "p6_gate_decided": p6_gate_decided}
_ = stop_run
