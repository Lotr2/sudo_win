"""Deterministic (no-LLM) steps and gate evidence, shared by every variant."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import gates
import guardrails
import payments
import reconcile
import refunds
import webhooks
import delivery
from core.conventions import ASSUMPTION, MOCKED, PAYMENT_TEST_LOG, PHASES, STUBBED, Step

if TYPE_CHECKING:
    from core.base_variant import BaseVariant

AGENT = {"phase2_step4": "gate", "phase3_step4": "qa_tests", "phase4_step2": "policy_drafter",
         "phase4_step3": "policy_drafter", "phase4_step4": "policy_drafter", "phase5_step3": "commerce",
         "phase5_step4": "commerce", "phase5_step5": "commerce", "phase6_step2": "metrics"}

EVIDENCE_STEPS = ("phase1_step2", "phase1_step4", "phase2_step1")


def _h(step: Step, idea: str) -> str:
    return f"# Phase {step.phase} ({PHASES[step.phase][0]}), step {step.num}: {step.title} [{idea}]\n\n_Agent: {AGENT.get(step.key, 'code')} · Model: none (deterministic code)_\n\n"


def evidence(v: "BaseVariant") -> dict:
    urls, total, assumptions = set(), 0, 0
    for k in EVIDENCE_STEPS:
        r = v.results.get(k)
        for c in (r.claims if r else []):
            total += 1
            if c["source_url"]:
                urls.add(c["source_url"])
            else:
                assumptions += 1
    demand = v.idea_decisions.get("demand_result")   # founder-entered only; never generated
    return {"independent_verified_sources": len(urls), "claims_total": total, "assumption_claims": assumptions,
            "assumption_ratio": (assumptions / total) if total else 1.0, "demand_result": demand,
            "verified_urls": sorted(urls)}


def _features(v) -> list[str]:
    r = v.results.get("phase3_step1")
    return [str(f) for f in (r.data.get("features") or [])] if r and not r.failed else []


def _payment_log(v, line: str) -> None:
    v.payment_log.append(line)
    text = (f"# Payment and delivery test log ({MOCKED})\n\nAll payments, webhooks, deliveries, refunds and payouts below are "
            f"{MOCKED}: no real or sandbox payment provider is used and no money moves.\n\n" + "\n".join(v.payment_log) + "\n")
    v.write_output(PAYMENT_TEST_LOG, text)


def run(v: "BaseVariant", step: Step) -> tuple[dict, str]:
    k, idea, h = step.key, v.idea, _h(step, v.idea)
    if k == "phase2_step4":
        e = evidence(v)
        e["thresholds"] = {"min_independent_sources": gates.MIN_INDEPENDENT_DEMAND_SOURCES,
                           "max_assumption_ratio": gates.MAX_ASSUMPTION_RATIO}
        return e, json.dumps(e, indent=1)
    if k == "phase3_step4":
        mvp = (v.results.get("phase3_step2").text if v.results.get("phase3_step2") else "").lower()
        docs = (v.results.get("phase3_step3").text if v.results.get("phase3_step3") else "").lower()
        feats = _features(v)
        failures = [f"feature not in MVP: {f}" for f in feats if f.lower() not in mvp]
        failures += [f"feature not in user docs: {f}" for f in feats if f.lower() not in docs]
        if not feats:
            failures.append("spec lists no features")
        for key in ("phase3_step2", "phase3_step3"):
            r = v.results.get(key)
            if not r or r.failed:
                failures.append(f"{key} missing or failed")
            else:
                failures += [f"guardrail {f.rule} in {key}" for f in guardrails.lint(r.text)]
        lines = [h, f"Result: **{'PASS' if not failures else 'FAIL'}**\n", f"Features checked: {len(feats)}\n"]
        lines += [f"- FAIL: {f}" for f in failures] or ["- All acceptance checks passed"]
        return {"tests_passed": not failures, "failures": failures}, "\n".join(lines) + "\n"
    if k in ("phase4_step2", "phase4_step3", "phase4_step4"):
        price = (v.results.get("phase4_step1").data.get("price_usd") if v.results.get("phase4_step1") else None)
        name = {"phase4_step2": "Terms of sale", "phase4_step3": "Privacy policy", "phase4_step4": "Refund policy"}[k]
        body = {
            "phase4_step2": f"- Product: {v.brief.idea} (digital download)\n- Price: {price} USD (one-time)\n- Delivery: automatic access link after payment\n- No income or results are promised.\n- The seller is the founder; this tool does not sell on their behalf.",
            "phase4_step3": "- Data collected at checkout: email address for delivery only.\n- No passwords or card numbers are stored by this system.\n- Data is not sold or shared for advertising.\n- Buyers can ask for deletion by email.",
            "phase4_step4": "- Full refund within 14 days of purchase, no questions asked.\n- A refund revokes access to the download.\n- Refunds are approved by the founder and processed by the payment provider.",
        }[k]
        text = f"{h}**DRAFT: not legal advice. The founder must review before use.** ({STUBBED} template, filled from the price step)\n\n## {name}\n\n{body}\n"
        return {"present": True}, text
    if k == "phase5_step3":
        st = v.commerce
        if not st.listing:
            _payment_log(v, f"- [{idea}] delivery test: store not open (founder did not approve) -> FAIL")
            v.invariants.update(store_opened_with_approval=False, delivered_once=False, replay_ignored=False)
            return {"failed": True}, h + "Store is not open (no founder approval), so no test order was possible.\n"
        ev = payments.simulate_test_order(st, f"{idea}-order-001")
        body, sig = webhooks.sign(ev)
        first = webhooks.handle(st, body, sig)
        second = webhooks.handle(st, body, sig)
        ok_dl = delivery.can_download(st, first.get("token", ""))
        v.invariants.update(store_opened_with_approval=True, delivered_once=first["status"] == "delivered" and len(st.deliveries) == 1,
                            replay_ignored=second["status"] == "duplicate_ignored", download_works=ok_dl)
        for line in (f"- [{idea}] {MOCKED} test order {ev['order_id']}: {ev['amount']:.2f} USD, fee {ev['fee']:.2f}",
                     f"- [{idea}] webhook {ev['id']} -> {first['status']} (token issued)",
                     f"- [{idea}] same webhook replayed -> {second['status']} (idempotency: no second delivery)",
                     f"- [{idea}] download with token -> {'allowed' if ok_dl else 'denied'}"):
            _payment_log(v, line)
            v.log("commerce", line.split("] ", 1)[1][:80], label=MOCKED)
        text = h + "\n".join(f"- {k2}: {'PASS' if val else 'FAIL'}" for k2, val in v.invariants.items()) + f"\n\nAll events {MOCKED}.\n"
        return {"failed": not all(v.invariants.values())}, text
    if k == "phase5_step4":
        st = v.commerce
        if not st.listing:
            v.invariants["refund_revokes_access"] = False
            return {"failed": True}, h + "Store is not open; refund test skipped.\n"
        ev = payments.simulate_test_order(st, f"{idea}-order-002")
        body, sig = webhooks.sign(ev)
        token = webhooks.handle(st, body, sig).get("token", "")
        prop = refunds.propose(st, ev["order_id"], "buyer request (MOCKED test)")
        v.log("refunds", "propose_refund", order_id=ev["order_id"], label=MOCKED)
        receipt = v.request_approval("phase5.step4.refund", "refund", prop["content"], prop["content"])
        if receipt is None:
            v.invariants["refund_revokes_access"] = False
            _payment_log(v, f"- [{idea}] refund for {ev['order_id']} proposed -> founder rejected; no refund")
            return {"failed": True}, h + "Founder rejected the proposed refund.\n"
        res = refunds.execute(st, ev["order_id"], content=prop["content"], receipt=receipt)
        revoked = not delivery.can_download(st, token)
        v.invariants["refund_revokes_access"] = res["status"] == "refunded" and revoked
        v.log("refunds", "execute_refund", approved_by="founder", order_id=ev["order_id"], label=MOCKED)
        for line in (f"- [{idea}] refund proposed for {ev['order_id']} -> founder approved -> {MOCKED} refund {prop['amount']:.2f} USD",
                     f"- [{idea}] download after refund -> {'denied (access revoked)' if revoked else 'STILL ALLOWED (FAIL)'}"):
            _payment_log(v, line)
        return {"failed": not v.invariants["refund_revokes_access"]}, h + f"- Refund executed after founder approval: {res['status']}\n- Access revoked: {revoked}\n\nAll events {MOCKED}.\n"
    if k == "phase5_step5":
        st = v.commerce
        payments.mock_payout(st)
        rows, balanced = reconcile.reconcile(st) if st.orders else ([{"order_id": "TOTAL", "matched": False, "label": MOCKED}], False)
        v.invariants["reconciliation_balanced"] = balanced
        _payment_log(v, f"- [{idea}] reconciliation orders/payments/fees/refunds/payouts -> {'balanced' if balanced else 'NOT balanced'}")
        return {"failed": not balanced}, reconcile.to_csv(rows)
    if k == "phase6_step2":
        st = v.commerce
        paid = [o for o in st.orders.values()]
        text = (h + f"**All numbers come from {MOCKED} test orders, not real sales.**\n\n"
                f"- Test orders: {len(paid)}\n- Refunded test orders: {len(st.refunds)}\n- Deliveries: {len(st.deliveries)}\n"
                f"- Real customers: none yet\n- Customer feedback: none collected\n")
        return {"mocked": True, "orders": len(paid)}, text
    raise KeyError(k)


def gate_result(v: "BaseVariant", phase: int) -> gates.GateResult:
    if phase == 1:
        excluded = [f.rule for f in guardrails.excluded_category(v.brief.text)]
        r = v.results.get("phase1_step2")
        pain = sum(1 for c in (r.claims if r else []) if c["source_url"])
        return gates.phase1(excluded=excluded, verified_pain=pain)
    if phase == 2:
        e = evidence(v)
        return gates.phase2(independent_sources=e["independent_verified_sources"],
                            assumption_ratio=e["assumption_ratio"], demand_result=e["demand_result"])
    if phase == 3:
        r = v.results.get("phase3_step4")
        return gates.phase3(tests_passed=bool(r and r.data.get("tests_passed")),
                            failures=(r.data.get("failures") if r else ["tests not run"]))
    if phase == 4:
        p = v.results.get("phase4_step1")
        m = v.results.get("phase1_step4")
        prices = [float(x) for x in ((m.data.get("competitor_prices_usd") if m else None) or []) if isinstance(x, (int, float)) and x > 0]
        if m and not any(c["source_url"] for c in m.claims):
            prices = []          # unsourced competitor prices cannot anchor the price
        present = all(v.results.get(k) for k in ("phase4_step2", "phase4_step3", "phase4_step4"))
        return gates.phase4(price=(p.data.get("price_usd") if p else None), competitor_prices=prices, policies_present=present)
    if phase == 5:
        inv = dict(v.invariants) or {"commerce_tests_ran": False}
        return gates.phase5(invariants=inv)
    if phase == 6:
        return gates.phase6(real_customer_evidence=bool(v.idea_decisions.get("real_customer_evidence")))
    raise ValueError(phase)


def gate_text(v: "BaseVariant", g: gates.GateResult) -> str:
    name, core = PHASES[g.phase]
    lines = [f"# Phase {g.phase} gate ({name}) [{v.idea}]: recommendation **{g.outcome.upper()}**", "",
             f"_Agent: gate · Model: none (pre-set rules in gates.py) · Core step: {core or 'none'}_", "",
             "## Why", ""] + [f"- {r}" for r in g.reasons] + ["", "## Evidence", ""]
    lines += [f"- {k}: {val}" for k, val in g.metrics.items()]
    lines += ["", f"Unsourced claims are labelled {ASSUMPTION}. The founder decides; the system only recommends.", ""]
    return "\n".join(lines)
