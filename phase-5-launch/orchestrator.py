"""Phase 5: Launch. Open the store and deliver automatically. Payments are MOCKED: no money moves.

  prepare    Step 1: store page copy -> founder approves -> published locally.
             Step 2: launch posts drafted for the founder to post themselves -> founder approves, edits or
                     discards each one. The system never posts anything.
             Step 3: delivery rule (email + "deliver when a payment succeeds") -> founder approves once.
  test-run   Payment and delivery test run on a throwaway database -> outputs/payment_test_log.md
  serve      Run the store: page, MOCKED checkout, webhooks, order pages, refund requests, product access.
  refunds    Founder reviews refund requests. Nothing is refunded without the founder.
  reconcile  Match orders, payments, fees, refunds, access and payouts.
  gate       Everything approved + test run passed -> founder opens the store -> handoff to Phase 6.

Usage:
  python phase-5-launch/orchestrator.py prepare [--handoff outputs/phase4_step4_handoff.json]
  python phase-5-launch/orchestrator.py test-run | gate | refunds | reconcile
  python phase-5-launch/orchestrator.py serve [--port 8002]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import delivery  # noqa: E402
import refunds  # noqa: E402
import store_page  # noqa: E402
import webhooks  # noqa: E402
from agents.writers import DraftRejected, LaunchWriter, StoreCopywriter, check_post  # noqa: E402
from common.approval import FounderQuit, ask, choose  # noqa: E402
from common.decision_log import DecisionLog  # noqa: E402
from common.guardrails import REFUSAL, screen_request  # noqa: E402
from common.llm import LLMClient, LLMError  # noqa: E402
from gates import launch_gate  # noqa: E402
from payment_test import render_log, run_payment_test  # noqa: E402
from payments import MockPaymentProvider, load_secret  # noqa: E402
from reconcile import reconcile  # noqa: E402
from server import Store, make_server  # noqa: E402
from store_db import connect  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_PER_CALL = 0  # local open-weight model
HANDOFF_IN = "phase4_step4_handoff.json"
TEST_JSON = "phase5_step3_payment_test.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LaunchPipeline:
    def __init__(self, llm=None, *, input_fn=input, output_fn=print, log_path=None, outputs_dir=None, runtime_dir=None,
                 handoff_path=None):
        self.llm = llm
        self.input_fn = input_fn
        self.out = output_fn
        self.log = DecisionLog(log_path or REPO_ROOT / "decision_log.json")
        self.outputs = Path(outputs_dir or REPO_ROOT / "outputs")
        self.runtime = Path(runtime_dir or os.environ.get("RUNTIME_DIR") or REPO_ROOT / "runtime")
        self.store_dir = self.runtime / "store"
        self.handoff_path = Path(handoff_path or self.outputs / HANDOFF_IN)

    # ---------- shared ----------

    def handoff(self) -> dict:
        if not self.handoff_path.exists():
            raise FileNotFoundError(f"{self.handoff_path} not found. Phase 4 must end with a founder-approved GO first.")
        return json.loads(self.handoff_path.read_text())

    def facts(self, h: dict) -> dict:
        spec, business, plan = h.get("spec") or {}, h.get("business") or {}, h.get("pricing") or {}
        return {"product": spec.get("product_name") or "the product", "price_line": h["price_line"],
                "billing_note": h["billing_note"], "refund_note": h["refund_note"],
                "features": [store_page.capability(s.get("story", "")) for s in spec.get("stories") or []],
                "out_of_scope": spec.get("out_of_scope") or [], "business_name": business.get("business_name", ""),
                "contact_email": business.get("contact_email", ""),
                "amount_cents": round(float(plan.get("price", 0)) * 100), "currency": plan.get("currency", "USD")}

    def policy_files(self, h: dict) -> dict:
        return {k: str(self.outputs / f) for k, f in (h.get("policies") or {}).items()}

    def product_dir(self, h: dict) -> Path:
        p = Path(h.get("product_dir") or self.runtime / "product")
        if not (p / "index.html").exists():
            raise FileNotFoundError(f"The Phase 3 product was not found at {p}. Run Phase 3 first.")
        return p

    def _write(self, name: str, text: str) -> Path:
        self.outputs.mkdir(parents=True, exist_ok=True)
        (self.outputs / name).write_text(text)
        return self.outputs / name

    def _log_llm(self, agent, action, results, **extra):
        for res in results:
            self.log.append(agent, action, model=res.model, cost=COST_PER_CALL,
                            tokens=res.prompt_tokens + res.completion_tokens, **extra)

    def _ask(self, prompt: str) -> str:
        while True:
            answer = ask(self.input_fn, prompt)
            hits = screen_request(answer)
            if not hits:
                return answer
            self.out(f"\n{REFUSAL}\n")
            self.log.append("guardrail", "refuse_request", outcome="refused", reasons=hits)

    def _logged(self, action: str) -> bool:
        return any(e.get("action") == action for e in self.log.entries())

    # ---------- prepare ----------

    def prepare(self) -> dict:
        try:
            h = self.handoff()
            self.log.append("orchestrator", "load_phase4_handoff", source_file=self.handoff_path.name)
            f = self.facts(h)
            if not self.store_step(h, f):
                return {"outcome": "stop", "step": "store_page"}
            if not self.posts_step(h):
                return {"outcome": "stop", "step": "launch_posts"}
            if not self.delivery_step(f):
                return {"outcome": "stop", "step": "delivery_rule"}
            self.out("\nPrepared. Next: `test-run` (checks payments and delivery, MOCKED), then `gate`.")
            return {"outcome": "prepared"}
        except FounderQuit:
            self.log.append("founder", "quit", approved_by="founder", outcome="stop")
            self.out("\nStopped by founder. Nothing was published, sent or spent.")
            return {"outcome": "stop"}

    def store_step(self, h, f) -> bool:
        self.out("\n=== Step 1: store page ===")
        writer, feedback = StoreCopywriter(self.llm), None
        while True:
            self.out("(thinking...)")
            try:
                copy, results, rejected = writer.write(h, feedback)
            except DraftRejected as e:
                self.log.append("guardrail", "reject_store_copy", outcome="rejected", reasons=e.issues)
                self.out(f"\nEvery draft broke the rules ({'; '.join(e.issues)}), so none is shown.")
                if choose(self.input_fn, self.out, "What next?", {"r": "Try again", "s": "Stop"}) == "s":
                    return False
                continue
            self._log_llm("store_copywriter", "write_store_copy", results)
            for issues in rejected:
                self.log.append("guardrail", "reject_store_copy", outcome="regenerated", reasons=issues)
            html = store_page.render_store(copy, f)
            preview = self._write("phase5_step1_store_page.html", html)
            self.out(f"\nHeadline: {copy['headline']}\nSubheadline: {copy['subheadline']}\n"
                     f"Price block (from Phase 4): {f['price_line']}. {f['billing_note']} {f['refund_note']}\n"
                     f"Full preview: {preview}")
            pick = choose(self.input_fn, self.out, "\nPublish this store page? (local only; checkout is MOCKED)",
                          {"p": "Approve and publish", "r": "Rewrite with my feedback", "s": "Stop"})
            if pick == "s":
                self.log.append("founder", "reject_store_page", approved_by="founder", outcome="stop")
                return False
            if pick == "r":
                feedback = self._ask("What should change?\n> ")
                continue
            record = store_page.publish(self.store_dir, html)
            self.log.append("founder", "publish_store_page", approved_by="founder", page_sha256=record["sha256"])
            return True

    def posts_step(self, h) -> bool:
        self.out("\n=== Step 2: launch posts (you post them yourself; this system never posts) ===\n(thinking...)")
        try:
            posts, results, rejected = LaunchWriter(self.llm).write(h)
            self._log_llm("launch_writer", "draft_launch_posts", results, posts=len(posts))
        except DraftRejected as e:
            self.log.append("guardrail", "reject_launch_posts", outcome="rejected", reasons=e.issues)
            self.out(f"\nEvery draft broke the rules ({'; '.join(e.issues)}). No posts to review.")
            posts = []
        reviewed = []
        for i, p in enumerate(posts, 1):
            while True:
                self.out(f"\n--- Post {i}/{len(posts)}: {p['channel']} ---\n{p['title']}\n\n{p['body']}\n\n"
                         f"(Etiquette: {p['etiquette']})")
                pick = choose(self.input_fn, self.out, "This post:", {"a": "Approve (I'll post it myself)",
                                                                      "e": "Replace the text", "x": "Discard",
                                                                      "s": "Stop"})
                if pick == "s":
                    return False
                if pick == "e":
                    body = self._ask("New text (one line; use {store_link} for the link):\n> ")
                    issues = check_post({**p, "body": body}, i)
                    if issues:
                        self.out("  ! " + "; ".join(issues))
                        continue
                    p = {**p, "body": body}
                    continue
                status = "approved" if pick == "a" else "discarded"
                self.log.append("founder", f"{'approve' if pick == 'a' else 'discard'}_launch_post", approved_by="founder",
                                channel=p["channel"])
                reviewed.append({**p, "status": status})
                break
        self.log.append("founder", "review_launch_posts", approved_by="founder",
                        approved=sum(p["status"] == "approved" for p in reviewed), discarded=sum(
                            p["status"] == "discarded" for p in reviewed))
        self._write("phase5_step2_launch_posts.md", render_posts_md(reviewed))
        return True

    def delivery_step(self, f) -> bool:
        self.out("\n=== Step 3: automatic delivery ===")
        rule_facts = {"product": f["product"], "price": f["price_line"], "refund_note": f["refund_note"],
                      "contact_email": f["contact_email"], "business_name": f["business_name"]}
        sample = delivery.EMAIL_TEMPLATE.format(link="https://<your store>/app/<private-token>/", **rule_facts)
        self.out("When a payment succeeds, the buyer gets access and this email (MOCKED: written to the outbox, "
                 f"not sent):\n\n{sample}")
        pick = choose(self.input_fn, self.out, "Approve this as a standing rule? (refunds still need you each time)",
                      {"a": "Approve automatic delivery", "s": "Stop"})
        if pick == "s":
            self.log.append("founder", "reject_delivery_rule", approved_by="founder", outcome="stop")
            return False
        delivery.approve_rule(self.store_dir / "delivery_rule.json", rule_facts)
        self.log.append("founder", "approve_delivery_rule", approved_by="founder")
        return True

    # ---------- test run ----------

    def test_run(self) -> dict:
        h = self.handoff()
        rule = delivery.load_rule(self.store_dir / "delivery_rule.json")
        store_page.approved_store(self.store_dir)
        self.out("Running the payment and delivery test (MOCKED payments, throwaway database)...")
        run = run_payment_test(self.runtime / "payment_test", self.store_dir, self.product_dir(h), self.facts(h),
                               rule, self.policy_files(h))
        when = _now()
        self._write("payment_test_log.md", render_log(run, when))
        self._write(TEST_JSON, json.dumps({**run, "run_at": when}, indent=2) + "\n")
        self.log.append("payment_test", "run_payment_test", outcome="pass" if run["passed"] else "fail",
                        scenarios=len(run["results"]), failed=[r["scenario"] for r in run["results"] if not r["passed"]])
        for r in run["results"]:
            self.out(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['scenario']}: {r['actual']}")
        return run

    # ---------- live store ----------

    def store(self, host="127.0.0.1", port=8002) -> Store:
        h = self.handoff()
        db = connect(self.store_dir / "store.db")
        secret = load_secret(self.store_dir / "webhook_secret")
        return Store(db, MockPaymentProvider(db, secret), secret, delivery.load_rule(self.store_dir / "delivery_rule.json"),
                     self.facts(h), self.store_dir, self.product_dir(h), self.policy_files(h), os.environ.get("PUBLIC_URL") or f"http://{host}:{port}")

    def review_refunds(self) -> int:
        st = self.store()
        decided = 0
        try:
            for r in refunds.pending(st.db):
                self.out(f"\nRefund {r['id']}: order {r['order_id']} ({r['email']}), "
                         f"{r['amount_cents'] / 100:.2f} {self.facts(self.handoff())['currency']}\n  Reason: {r['reason'] or '-'}")
                pick = choose(self.input_fn, self.out, "Decision:", {"a": "Approve the refund (MOCKED, revokes access)",
                                                                     "d": "Deny", "k": "Decide later"})
                if pick == "k":
                    continue
                if pick == "a":
                    payload, sig = refunds.approve(st.db, st.provider, r["id"], approved_by="founder")
                    result = webhooks.handle(st.db, payload, sig, st.secret, st.rule, st.base_url)
                    self.log.append("founder", "approve_refund", approved_by="founder", refund_id=r["id"], result=result)
                else:
                    refunds.deny(st.db, r["id"], approved_by="founder")
                    self.log.append("founder", "deny_refund", approved_by="founder", refund_id=r["id"])
                decided += 1
        except FounderQuit:
            pass
        if not decided:
            self.out("No refund decisions made.")
        return decided

    def reconcile_store(self) -> dict:
        st = self.store()
        rec = reconcile(st.db)
        self._write("phase5_step4_reconciliation.md", render_reconciliation_md(rec))
        self.log.append("reconciler", "reconcile", mismatches=len(rec["mismatches"]))
        self.out(f"Orders {rec['orders']}, net {rec['net_cents']} cents, mismatches: {len(rec['mismatches'])}")
        for m in rec["mismatches"]:
            self.out(f"  ! {m}")
        return rec

    # ---------- gate ----------

    def gate(self) -> dict:
        try:
            return self._gate()
        except FounderQuit:
            return {"outcome": "quit"}

    def _gate(self) -> dict:
        h = self.handoff()
        try:
            store_page.approved_store(self.store_dir)
            store_ok = (True, "published page matches the approved version")
        except PermissionError as e:
            store_ok = (False, str(e))
        posts_ok = (True, "reviewed") if self._logged("review_launch_posts") else (False, "run `prepare`")
        try:
            delivery.load_rule(self.store_dir / "delivery_rule.json")
            rule_ok = (True, "approved")
        except delivery.DeliveryNotApproved as e:
            rule_ok = (False, str(e))
        missing = [k for k, p in self.policy_files(h).items() if not Path(p).exists()]
        policies_ok = (not missing and bool(self.policy_files(h)), f"missing: {', '.join(missing)}" if missing else "present")
        test_path = self.outputs / TEST_JSON
        run = json.loads(test_path.read_text()) if test_path.exists() else None

        gate = launch_gate(store_ok, posts_ok, rule_ok, run, policies_ok)
        self.out("\n=== Launch gate ===\n")
        for c in gate.checks:
            self.out(f"  [{'x' if c.passed else ' '}] {c.name}  ({c.detail})")
        self.out(f"\n  OUTCOME: {gate.outcome.upper()}")
        self.log.append("gate", "launch_gate", outcome=gate.outcome,
                        failed_checks=[c.name for c in gate.checks if not c.passed])

        options = {"o": "Open the store (local, MOCKED payments): move to Phase 6", "s": "Not yet"} \
            if gate.outcome == "go" else {"s": "Fix the failing checks first"}
        pick = choose(self.input_fn, self.out, "\nWhat next?", options)
        decision = "open_store" if pick == "o" else "not_yet"
        self.log.append("founder", "launch_gate_decision", approved_by="founder", outcome=gate.outcome,
                        founder_decision=decision)
        self._write("phase5_gate_launch.md", render_gate_md(gate, decision))
        if decision == "open_store":
            self._write("phase5_step5_handoff.json", json.dumps({
                **h, "store": {k: v for k, v in self.facts(h).items()}, "store_opened_at": _now(),
                "payment_test": {"passed": run["passed"], "run_at": run.get("run_at")},
            }, indent=2) + "\n")
            self.out("\nStore approved to open. Run `serve` to take (MOCKED) orders. Handed to Phase 6.")
        return {"outcome": gate.outcome, "decision": decision}


# ---------- rendering ----------

def render_posts_md(posts: list[dict]) -> str:
    lines = ["# Phase 5, Step 2: launch posts", "",
             "Drafted for the founder to post **themselves, under their own name**. This system does not post, "
             "schedule or send anything. Replace `{store_link}` with the store's address when posting.", ""]
    if not posts:
        lines.append("No posts were approved.")
    for i, p in enumerate(posts, 1):
        lines += [f"## {i}. {p['channel']} ({p['status'].upper()})", "", f"**{p['title']}**" if p["title"] else "", "",
                  p["body"], "", f"*Etiquette:* {p['etiquette']}", ""]
    return "\n".join(lines) + "\n"


def render_reconciliation_md(rec: dict) -> str:
    lines = ["# Phase 5, Step 4: reconciliation (MOCKED payments)", "",
             "| Item | Amount (cents) |", "|---|---|",
             f"| Gross payments | {rec['gross_cents']} |", f"| Fees (MOCKED schedule) | {rec['fees_cents']} |",
             f"| Refunded | {rec['refunded_cents']} |", f"| Net | {rec['net_cents']} |",
             f"| Paid out | {rec['paid_out_cents']} |", f"| Balance not yet paid out | {rec['balance_cents']} |", "",
             f"Orders: {rec['orders']} ({', '.join(f'{k}: {v}' for k, v in rec['by_status'].items()) or 'none'})", "",
             "## Mismatches", ""]
    lines += [f"- {m}" for m in rec["mismatches"]] or ["- None. Orders, payments, fees, refunds, access and payouts match."]
    return "\n".join(lines) + "\n"


def render_gate_md(gate, decision) -> str:
    lines = ["# Phase 5 gate decision: launch", "", f"**Outcome:** {gate.outcome.upper()}", "",
             f"**Founder decision:** {decision}", "", "## Checks", ""]
    lines += [f"- [{'x' if c.passed else ' '}] {c.name}: {c.detail}" for c in gate.checks]
    lines += ["", "Payments are MOCKED: opening the store means taking simulated orders locally. No money moves."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 5: Launch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("prepare")
    sp.add_argument("--handoff")
    for name in ("test-run", "gate", "refunds", "reconcile"):
        sub.add_parser(name)
    ss = sub.add_parser("serve")
    ss.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ss.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8002)))
    args = p.parse_args(argv)
    try:
        if args.cmd == "prepare":
            llm = LLMClient()
            print(f"Model: {llm.model} at {llm.base_url}")
            LaunchPipeline(llm, handoff_path=args.handoff).prepare()
        elif args.cmd == "test-run":
            run = LaunchPipeline().test_run()
            return 0 if run["passed"] else 1
        elif args.cmd == "gate":
            LaunchPipeline().gate()
        elif args.cmd == "refunds":
            LaunchPipeline().review_refunds()
        elif args.cmd == "reconcile":
            LaunchPipeline().reconcile_store()
        else:
            pipe = LaunchPipeline()
            server = make_server(pipe.store(args.host, args.port), args.host, args.port)
            print(f"Store open at http://{args.host}:{args.port}  (payments MOCKED; Ctrl+C to stop)")
            server.serve_forever()
    except (FileNotFoundError, PermissionError, delivery.DeliveryNotApproved, LLMError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Nothing was published, sent or spent.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
