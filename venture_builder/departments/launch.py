"""Phase 5 · Launch (sell, deliver and get paid): transactional outbox + human approval gate; sale to cash
is event-driven plain code. Publishing and payments are MOCKED.

Liaison asks channels and tone → Launch Writers → Policy Guard → Approval Outbox → founder approves or
rejects each item → MOCKED publish adapter → payment test run (checkout → idempotent delivery → founder-
approved refund revokes access → reconciliation) → Gate 5.
"""

from __future__ import annotations

from .. import gates, reconcile, webhooks
from ..agents import launch_writers
from ..approval import NotApproved
from .. import labels
from ..context import Ask, Context, Next
from ..decision_log import log_action, now_iso
from ..events import EventStore
from ..payments import MockPaymentProvider
from ..refunds import execute_refund, request_refund
from .common import decision, gate_markdown, items_payload, present_gate, stop_run


def _events(ctx: Context) -> EventStore:
    return EventStore(ctx.store.path("events.db"))


def p5_start(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    d.phase = 5
    ctx.note("Phase 5 · Launch started (publishing and payments MOCKED)")
    from .common import kickoff
    kickoff(ctx, "Launch: draft the store page and posts for your approval; test sale to cash (MOCKED)",
            ["the channels you actually use", "the price and refund window from Phase 4"])
    found = [f["claim"] for f in d.findings.get(d.chosen_variant, []) if f["kind"] == "channel"][:4]
    q = ("Which channels do you actually use to reach these customers, and what tone should the posts have? "
         + (f"Research found: {'; '.join(found)}." if found else ""))
    ctx.say(q + " Reminder: publishing is MOCKED; nothing is posted.", kind="question")
    return Ask("question", {"question": q, "why": "channels and tone"}, resume_to="p5_channels",
               timeout=ctx.question_timeout())


def p5_channels(ctx: Context, answer) -> Next:
    d = ctx.dossier
    text = answer if isinstance(answer, str) else (answer or {}).get("text") if answer else None
    if text:
        d.founder_inputs.append({"phase": 5, "type": "founder_reported", "text": text})
        d.channels = [c.strip() for c in text.replace(" and ", ",").split(",") if c.strip()][:4]
    if not d.channels:
        d.channels = ["email newsletter", "a relevant online community"]
    return Next("p5_drafts")


def _guarded_drafts(ctx: Context, note: str = ""):
    d = ctx.dossier
    payload = {"product": d.spec.model_dump(include={"title", "summary", "features"}),
               "price": d.pricing.model_dump(include={"currency", "price", "tiers"}),
               "refund_window_days": d.policies.refund_window_days, "channels": d.channels,
               "tone_and_notes": [x["text"] for x in d.founder_inputs if x.get("phase") == 5] + ([note] if note else []),
               "rules": "Posts are drafts in the founder's own voice for them to publish. No testimonials, user "
                        "counts, urgency, countdowns or income claims."}
    for _ in (1, 2):
        drafts = launch_writers.run(ctx, payload)
        texts = [("store_page", " ".join([drafts.store_page.headline, drafts.store_page.description,
                                          *drafts.store_page.whats_included]))]
        texts += [(f"post:{p.channel}", p.text) for p in drafts.posts]
        blocked = []
        for subject, text in texts:
            hit = ctx.guard.check(text, context="outbox", author_family=ctx.author_family("launch_writers"),
                                  subject=subject)
            if not hit.allowed:
                blocked.append(f"{subject}: {hit.reason}")
        if not blocked:
            return drafts
        payload["rejected_by_policy_guard"] = blocked
    raise RuntimeError("launch drafts failed the Policy Guard twice: " + "; ".join(blocked))


def p5_drafts(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    drafts = _guarded_drafts(ctx)
    d.launch = drafts
    sp = drafts.store_page
    store_md = [f"# {sp.headline}", "", sp.description, "", "## What's included", ""] + \
               [f"- {x}" for x in sp.whats_included] + ["", f"**{sp.price_line}**", "", sp.refund_line, "",
                                                        "Checkout and payments are MOCKED in this build."]
    ctx.store.write_output("phase5_step1_store_page.md", "\n".join(store_md) + "\n")
    posts_md = ["# Phase 5 · Step 2: Launch posts (drafts for the founder to publish under their own name)", ""]
    for p in drafts.posts:
        posts_md += [f"## {p.channel}", "", p.text, ""]
    ctx.store.write_output("phase5_step2_launch_posts.md", "\n".join(posts_md))
    ids = [ctx.outbox.propose(kind="store_page", action="publish", title="Publish the store page (MOCKED)",
                              content=sp.model_dump(), phase=5)["id"]]
    for p in drafts.posts:
        ids.append(ctx.outbox.propose(kind="post", action="publish", title=f"Post on {p.channel} (MOCKED)",
                                      content=p.model_dump(), phase=5)["id"])
    d.launch_items = ids
    ctx.say(f"Your store page and launch posts are ready: {len(ids)} item(s) to review. Please approve each one or "
            "ask for changes. Practice mode: nothing is actually posted (MOCKED).", kind="approval")
    return Ask("approval", items_payload(ctx, ids), resume_to="p5_publish")


def p5_publish(ctx: Context, answer) -> Next | Ask:
    d = ctx.dossier
    revised = []
    for i in d.launch_items:
        item = ctx.outbox.get(i)
        if item["status"] == "approved":
            ctx.outbox.publish(i, adapter="MOCKED channel publisher")
        elif item["status"] == "rejected" and d.count(f"rewrite_{i}") < 1:
            d.bump(f"rewrite_{i}")
            ctx.say(f"You rejected '{item['title']}' with the note: {item['note'] or '(none)'}. I'll pass it to the "
                    "Launch Writers.")
            drafts = _guarded_drafts(ctx, note=f"Revise {item['kind']}: {item['note']}")
            content = drafts.store_page.model_dump() if item["kind"] == "store_page" else next(
                (p.model_dump() for p in drafts.posts if p.channel == item["content"].get("channel")),
                drafts.posts[0].model_dump())
            ctx.outbox.update_content(i, content)
            revised.append(i)
    if revised:
        return Ask("approval", items_payload(ctx, revised), resume_to="p5_publish")
    return Next("p5_sale")


def p5_sale(ctx: Context, answer) -> Ask:
    """Payment test run, part 1: checkout → payment → delivery; duplicate event; refund request."""
    d = ctx.dossier
    store = _events(ctx)
    pay = MockPaymentProvider(store)
    log: list[str] = []
    price = d.pricing.price
    o1 = pay.create_checkout(d.spec.title, price)
    ev = pay.succeed(o1)
    r1 = webhooks.handle(store, ev)
    log.append(f"1. Checkout {o1} (${price:g}) → payment.succeeded {ev['event_id']} → webhook: **{r1}**")
    r2 = webhooks.handle(store, ev)
    tokens = store.rows("SELECT COUNT(*) AS n FROM deliveries WHERE order_id=?", (o1,))[0]["n"]
    log.append(f"2. Same event replayed → webhook: **{r2}**; deliveries for {o1}: {tokens}")
    o2 = pay.create_checkout(d.spec.title, price)
    ev2 = pay.succeed(o2)
    webhooks.handle(store, ev2)
    item = request_refund(ctx.outbox, o2, "Buyer asked for a refund within the window (MOCKED test)")
    try:
        execute_refund(ctx.outbox, store, item["id"])
        log.append("3. ERROR: refund executed without approval")
    except NotApproved:
        status = store.rows("SELECT status FROM deliveries WHERE order_id=?", (o2,))[0]["status"]
        log.append(f"3. Refund for {o2} attempted without founder approval → refused; access still **{status}**")
    d.payment_test = {"log": log, "orders": [o1, o2], "refund_item": item["id"], "duplicate_ok": r2 == "duplicate"
                      and tokens == 1, "first_delivered": r1 == "delivered"}
    ctx.say(f"Payment test: a refund for {o2} is waiting for your approval (MOCKED; no money moves).",
            kind="approval")
    return Ask("approval", items_payload(ctx, [item["id"]]), resume_to="p5_sale_refund")


def p5_sale_refund(ctx: Context, answer) -> Next:
    """Payment test run, part 2: founder-approved refund revokes access; reconciliation."""
    d = ctx.dossier
    store = _events(ctx)
    pay = MockPaymentProvider(store)
    pt = d.payment_test
    o2 = pt["orders"][1]
    item = ctx.outbox.get(pt["refund_item"])
    revoked_ok = False
    if item["status"] == "approved":
        res = execute_refund(ctx.outbox, store, item["id"])
        status = store.rows("SELECT status FROM deliveries WHERE order_id=?", (o2,))[0]["status"]
        revoked_ok = res == "revoked" and status == "revoked"
        pt["log"].append(f"4. Founder approved the refund → refund.succeeded (MOCKED) → access **{status}**")
    else:
        pt["log"].append(f"4. Founder rejected the refund ({item['note'] or 'no note'}) → nothing happened")
        revoked_ok = None
    pay.payout()
    books = reconcile.reconcile(store)
    pt["log"].append(f"5. Reconciliation: {books['orders']} orders, gross ${books['gross']:.2f}, fees "
                     f"${books['fees']:.2f}, refunds ${books['refunds']:.2f}, payout ${books['payouts']:.2f} → "
                     f"{len(books['mismatches'])} mismatch(es)")
    scratch = store.copy_to(ctx.store.path("events_broken_copy.db"))
    scratch.db.execute("INSERT INTO deliveries VALUES ('ord_999','planted-token','active',?,NULL)", (now_iso(),))
    broken = reconcile.reconcile(scratch)
    flagged = any("ord_999" in m for m in broken["mismatches"])
    pt["log"].append(f"6. Scratch copy with a deliberately broken record (delivery with no payment) → "
                     f"reconciliation flags it: **{flagged}** ({'; '.join(broken['mismatches'])})")
    passed = bool(pt["first_delivered"] and pt["duplicate_ok"] and revoked_ok is not False and not books["mismatches"]
                  and flagged)
    pt.update({"books": books, "broken_flagged": flagged, "revoked_ok": revoked_ok, "passed": passed})
    log_action("payment_test", "payment_test_run", passed=passed)
    md = ["# Payment and delivery test log", "", "All payment events are **MOCKED**: no provider, no keys, no card "
          "data, no real money.", "", f"Run: `{d.run_id}`", ""] + pt["log"] + [
          "", f"**Result: {'PASS' if passed else 'FAIL'}**", "", "Checks: one payment delivers once; a duplicate "
          "event does not deliver twice; a refund without founder approval does nothing; an approved refund "
          "revokes access; reconciliation matches orders, payments, fees, refunds and payouts and flags a "
          "deliberately broken record."]
    ctx.store.write_output("payment_test_log.md", "\n".join(md) + "\n")
    return Next("p5_gate")


def p5_gate(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    items = [ctx.outbox.get(i) for i in d.launch_items]
    res = gates.gate5(items=items, delivery_tests_passed=bool(d.payment_test.get("passed")), withdrawn=False, t=ctx.t)
    memo = {"summary": f"The team recommends: {labels.outcome_name(res.label, 5)}.",
            "explanation": "This check makes sure every page and post was approved by the founder before anything went "
                           "out, and runs a practice sale: one payment delivers the product exactly once, a repeated "
                           "payment message does not deliver twice, a refund only happens after the founder approves it "
                           "and then removes access, and the books add up. Publishing and payments are MOCKED, so "
                           "nothing went online and no money moved (see payment_test_log.md).",
            "what_would_change_it": ["Connect a real payment provider in test mode and repeat the practice sale",
                                     "Publish the approved pages yourself when you're ready"]}
    md = gate_markdown(ctx, res, memo, "Put it in front of people and get it to buyers", "gate5")
    return present_gate(ctx, res, md, "p5_gate_decided")


def p5_gate_decided(ctx: Context, answer) -> Next:
    d = ctx.dossier
    rec = decision(ctx, 5)
    if rec["decision"] == "stop":
        return Next(stop_run(ctx, 5, "The founder withdrew the launch."))
    if rec["decision"] == "repeat":
        d.bump("gate5_repeat")
        return Next("p5_drafts")
    return Next("p6_start")


NODES = {"p5_start": p5_start, "p5_channels": p5_channels, "p5_drafts": p5_drafts, "p5_publish": p5_publish,
         "p5_sale": p5_sale, "p5_sale_refund": p5_sale_refund, "p5_gate": p5_gate, "p5_gate_decided": p5_gate_decided}
