"""Phase 6: Operate and learn. Support customers, measure, and decide: persevere, pivot or stop.

  targets  Step 1: founder sets the review targets BEFORE looking at results (defaults from Phase 4's break-even);
           they are locked by a hash in the decision log.
  support  Step 2: support agent drafts a reply grounded in the user guide and policies -> founder approves,
           edits or discards. Sending is MOCKED.
  metrics  Step 3: operating metrics from the store, waitlist and support tickets (aggregates only).
  review   Step 4: locked targets + metrics -> persevere / pivot / stop / repeat (code decides), analyst writes
           the memo, founder decides. A pivot writes a new brief for Phase 1, closing the loop.

Usage:
  python phase-6-operate-and-learn/orchestrator.py targets | metrics | review
  python phase-6-operate-and-learn/orchestrator.py support [--email buyer@example.com] [--message "..."]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metrics as metrics_mod  # noqa: E402
import support  # noqa: E402
from agents.analyst import Analyst  # noqa: E402
from agents.support_agent import DraftRejected, SupportAgent  # noqa: E402
from common.approval import FounderQuit, ask, choose  # noqa: E402
from common.decision_log import DecisionLog  # noqa: E402
from common.guardrails import REFUSAL, screen_request  # noqa: E402
from common.llm import LLMClient, LLMError  # noqa: E402
from gates import TargetsTampered, default_targets, review_gate, targets_hash, validate_targets, verify  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_PER_CALL = 0  # local open-weight model
HANDOFF_IN = "phase5_step5_handoff.json"
TARGETS_FILE = "phase6_step1_targets.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def break_even(pricing: dict) -> int | None:
    per = {"monthly": pricing.get("price", 0), "yearly": pricing.get("price", 0) / 12}.get(
        pricing.get("billing"), pricing.get("price", 0))
    costs = pricing.get("monthly_costs", 0) or 0
    return math.ceil(costs / per) if per and costs else (0 if per else None)


class OperatePipeline:
    def __init__(self, llm=None, *, input_fn=input, output_fn=print, log_path=None, outputs_dir=None, runtime_dir=None,
                 now=None):
        self.llm = llm
        self.input_fn = input_fn
        self.out = output_fn
        self.log = DecisionLog(log_path or REPO_ROOT / "decision_log.json")
        self.outputs = Path(outputs_dir or REPO_ROOT / "outputs")
        self.runtime = Path(runtime_dir or os.environ.get("RUNTIME_DIR") or REPO_ROOT / "runtime")
        self.tickets = support.Tickets(self.runtime / "support")
        self.now = now

    # ---------- shared ----------

    def handoff(self) -> dict:
        path = self.outputs / HANDOFF_IN
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Phase 5 must end with the founder opening the store.")
        return json.loads(path.read_text())

    def _write(self, name: str, text: str) -> Path:
        self.outputs.mkdir(parents=True, exist_ok=True)
        (self.outputs / name).write_text(text)
        return self.outputs / name

    def _ask(self, prompt: str) -> str:
        while True:
            answer = ask(self.input_fn, prompt)
            hits = screen_request(answer)
            if not hits:
                return answer
            self.out(f"\n{REFUSAL}\n")
            self.log.append("guardrail", "refuse_request", outcome="refused", reasons=hits)

    def _log_llm(self, agent, action, results, **extra):
        for res in results if isinstance(results, list) else [results]:
            self.log.append(agent, action, model=res.model, cost=COST_PER_CALL,
                            tokens=res.prompt_tokens + res.completion_tokens, **extra)

    def metrics(self, h: dict | None = None) -> dict:
        h = h or self.handoff()
        return metrics_mod.compute(self.runtime / "store" / "store.db", self.runtime / "waitlist" / "waitlist.csv",
                                   self.tickets.path, h.get("store_opened_at"), h.get("pricing") or {},
                                   break_even(h.get("pricing") or {}), self.now)

    # ---------- step 1: targets ----------

    def set_targets(self) -> dict | None:
        try:
            h = self.handoff()
            t = default_targets(break_even(h.get("pricing") or {}))
            self.out("\n=== Step 1: review targets (set them BEFORE looking at results) ===\n"
                     f"Default paying-customer target = break-even from Phase 4 ({break_even(h.get('pricing') or {})}).")
            while True:
                problems = validate_targets(t)
                for k, v in t.items():
                    self.out(f"  {k.replace('_', ' ')}: {v}")
                for p in problems:
                    self.out(f"  ! {p}")
                opts = {"e": "Edit", "s": "Stop"}
                if not problems:
                    opts = {"a": "Approve and lock these targets", **opts}
                pick = choose(self.input_fn, self.out, "\nOnce locked, targets can't change after results come in.", opts)
                if pick == "s":
                    return None
                if pick == "a":
                    locked = {"targets": t, "locked_at": _now(), "sha256": targets_hash(t)}
                    self._write(TARGETS_FILE, json.dumps(locked, indent=2) + "\n")
                    self.log.append("founder", "approve_review_targets", approved_by="founder",
                                    targets_sha256=locked["sha256"], **t)
                    return locked
                for k in t:
                    v = ask(self.input_fn, f"{k.replace('_', ' ')} [{t[k]}]: ", allow_empty=True)
                    if v:
                        try:
                            t[k] = float(v) if k == "max_refund_rate" else int(v)
                        except ValueError:
                            self.out(f"  ! '{v}' is not a number; kept {t[k]}")
        except FounderQuit:
            return None

    # ---------- step 2: support ----------

    def support_reply(self, email: str | None = None, message: str | None = None) -> dict | None:
        try:
            return self._support(email, message)
        except FounderQuit:
            return None

    def _support(self, email, message):
        h = self.handoff()
        message = message or ask(self.input_fn, "Paste the customer's message (one line):\n> ")
        guide = (self.outputs / "phase3_step4_user_docs.md")
        policy_text = "\n\n".join((self.outputs / f).read_text() for f in (h.get("policies") or {}).values()
                                  if (self.outputs / f).exists())
        context = {"facts": h.get("store") or {}, "guide": guide.read_text() if guide.exists() else "(no user guide)",
                   "policies": policy_text or "(no policies)"}
        orders = support.order_status(self.runtime / "store" / "store.db", email)
        self.out("(drafting reply...)")
        try:
            draft, results, rejected = SupportAgent(self.llm).draft(context, message, orders)
        except DraftRejected as e:
            self.log.append("guardrail", "reject_support_reply", outcome="rejected", reasons=e.issues)
            self.out(f"Every draft broke the rules ({'; '.join(e.issues)}). Reply to this one yourself.")
            self.tickets.add(email, message, {"category": "other", "summary": "Draft failed checks"}, "needs_founder", None)
            self._write("phase6_step2_support_summary.md", support.summary_md(self.tickets.all()))
            return {"status": "needs_founder"}
        self._log_llm("support_agent", "draft_support_reply", results, category=draft["category"])
        for issues in rejected:
            self.log.append("guardrail", "reject_support_reply", outcome="regenerated", reasons=issues)
        signature = f"\n\n{(h.get('store') or {}).get('business_name', '')}\n{(h.get('store') or {}).get('contact_email', '')}"
        reply = draft["reply"]
        while True:
            self.out(f"\n[{draft['category']}] {draft['summary']}\n"
                     + (f"Note for you: {draft['internal_note']}\n" if draft["internal_note"] else "")
                     + f"\n--- Draft reply ---\n{reply}{signature}\n")
            pick = choose(self.input_fn, self.out, "This reply:", {"a": "Approve and send (MOCKED: saved, not emailed)",
                                                                   "e": "Replace the text", "x": "Discard; I'll handle it",
                                                                   "s": "Stop"})
            if pick == "s":
                return None
            if pick == "e":
                new = self._ask("New reply text (one line):\n> ")
                issues = support.check_reply(new)
                if issues:
                    self.out("  ! " + "; ".join(issues))
                    continue
                reply = new
                continue
            status = "replied" if pick == "a" else "discarded"
            ticket = self.tickets.add(email, message, draft, status, reply + signature if pick == "a" else None)
            self.log.append("founder", "approve_support_reply" if pick == "a" else "discard_support_reply",
                            approved_by="founder", ticket=ticket["id"], category=draft["category"],
                            sent="MOCKED" if pick == "a" else None)
            self._write("phase6_step2_support_summary.md", support.summary_md(self.tickets.all()))
            return {"status": status, "category": draft["category"]}

    # ---------- step 3: metrics ----------

    def write_metrics(self) -> dict:
        m = self.metrics()
        self._write("phase6_step3_metrics.md", render_metrics_md(m))
        self.log.append("metrics", "compute_metrics", paying_customers=m["paying_customers"],
                        store_visitors=m["store_visitors"])
        self.out(render_metrics_md(m))
        return m

    # ---------- step 4: review ----------

    def review(self) -> dict:
        try:
            return self._review()
        except FounderQuit:
            return {"outcome": "quit"}

    def _review(self) -> dict:
        h = self.handoff()
        tpath = self.outputs / TARGETS_FILE
        if not tpath.exists():
            raise FileNotFoundError("No review targets. Run `targets` first (before looking at results).")
        locked = json.loads(tpath.read_text())
        approvals = [e for e in self.log.entries() if e.get("action") == "approve_review_targets"]
        verify(locked, approvals[-1].get("targets_sha256") if approvals else None)

        m = self.metrics(h)
        self._write("phase6_step3_metrics.md", render_metrics_md(m))
        gate = review_gate(locked["targets"], m)
        self.log.append("gate", "review_gate", outcome=gate.outcome, reasons=gate.reasons)
        self.out(f"\n=== Review gate ===\n\n  OUTCOME: {gate.outcome.upper()}\n" + "\n".join(f"   - {r}" for r in gate.reasons))

        tickets = self.tickets.all()
        support_summary = {"by_category": m["support_by_category"],
                           "summaries": [support.redact(t["summary"]) for t in tickets][-20:],
                           "feature_requests": [support.redact(t["feature_request"]) for t in tickets if t.get("feature_request")],
                           "bugs": [support.redact(t["bug"]) for t in tickets if t.get("bug")]}
        self.out("(analyst writing the memo...)")
        memo, res = Analyst(self.llm).memo(gate.outcome, gate.reasons, m, locked["targets"], support_summary,
                                           h.get("concept") or {"product": (h.get("spec") or {}).get("summary")},
                                           (h.get("canvas") or {}).get("riskiest_assumptions") or [])
        self._log_llm("analyst", "write_persevere_pivot_memo", res, outcome=gate.outcome)
        self._write("phase6_step4_memo.md", render_memo_md(gate, m, locked["targets"], memo))
        self.out(f"\n{memo['headline']}\n\nMemo: {self.outputs / 'phase6_step4_memo.md'}")

        options = {
            "persevere": {"c": "Persevere: keep operating", "p": "Pivot anyway", "s": "Stop and wind down"},
            "pivot": {"p": "Pivot: start a new cycle from Phase 1 with what we learned", "s": "Stop and wind down"},
            "stop": {"p": "Start a new cycle with a different idea", "s": "Stop and wind down"},
            "repeat": {"k": "Keep operating and review again later", "s": "Stop and wind down"},
        }[gate.outcome]
        pick = choose(self.input_fn, self.out, f"\nOutcome: {gate.outcome.upper()}. Your decision:", options)
        decision = {"c": "persevere", "p": "pivot", "s": "stop", "k": "keep_operating"}[pick]
        self.log.append("founder", "review_decision", approved_by="founder", outcome=gate.outcome,
                        founder_decision=decision)
        if decision == "pivot":
            for i, o in enumerate(memo["pivot_options"], 1):
                self.out(f"  [{i}] ({o['change']}) {o['option']}")
            direction = self._ask("Describe the new direction (or type an option number):\n> ")
            if direction.isdigit() and 1 <= int(direction) <= len(memo["pivot_options"]):
                direction = memo["pivot_options"][int(direction) - 1]["option"]
            brief = dict(h.get("brief") or {})
            learned = "; ".join(memo["learnings"][:4]) or "; ".join(gate.reasons)
            brief["idea"] = f"{direction} (Pivot from: {brief.get('idea', '')}. Learned last cycle: {learned})"
            self._write("phase6_step5_next_brief.json", json.dumps(brief, indent=2) + "\n")
            self.out("\nNew cycle: python phase-1-discover/orchestrator.py --brief outputs/phase6_step5_next_brief.json")
        elif decision == "stop":
            self.out("\nWind-down steps are in the memo. Existing buyers keep what they paid for; refunds follow the "
                     "policy and need your approval. Nothing is sent or refunded automatically.")
        self._write("phase6_gate_review.md", render_gate_md(gate, locked, decision))
        return {"outcome": gate.outcome, "decision": decision}


# ---------- rendering ----------

def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1%}"


def _money(c) -> str:
    return "n/a" if c is None else f"{c / 100:,.2f}"


def render_metrics_md(m: dict) -> str:
    return "\n".join([
        "# Phase 6, Step 3: operating metrics", "",
        "Computed by code from the store database, the waitlist and support tickets. Payments are **MOCKED**, so "
        "revenue figures are simulated. Aggregates only; no personal data.", "",
        "| Metric | Value |", "|---|---|",
        f"| Days open | {m['days_open']} |", f"| Store visitors (unique browsers) | {m['store_visitors']} |",
        f"| Checkouts started | {m['checkouts_started']} |", f"| Paying customers (not refunded) | {m['paying_customers']} |",
        f"| Refunded | {m['refunded']} |", f"| Failed payments | {m['failed_payments']} |",
        f"| Visit to purchase | {_pct(m['visit_to_purchase'])} |", f"| Checkout completion | {_pct(m['checkout_completion'])} |",
        f"| Refund rate | {_pct(m['refund_rate'])} |",
        f"| Gross / fees / refunds / net | {_money(m['gross_cents'])} / {_money(m['fees_cents'])} / "
        f"{_money(m['refunded_cents'])} / {_money(m['net_cents'])} (MOCKED) |",
        f"| Monthly recurring revenue | {_money(m['mrr_cents'])} (MOCKED) |",
        f"| Break-even customers (Phase 4) | {m['break_even_customers']} |",
        f"| Waitlist size / bought | {m['waitlist_size']} / {m['buyers_from_waitlist']} |",
        f"| Support tickets | {m['support_tickets']} ({', '.join(f'{k}: {v}' for k, v in m['support_by_category'].items()) or 'none'}) |",
        f"| Product usage | {m['product_usage']} |", ""])


def render_memo_md(gate, m, t, memo) -> str:
    lines = ["# Phase 6, Step 4: persevere-or-pivot memo", "",
             f"**Decision rule outcome: {gate.outcome.upper()}** (decided by fixed rules from targets locked before "
             f"launch; the analyst explains it but cannot change it)", ""]
    lines += [f"- {r}" for r in gate.reasons]
    lines += ["", "## Targets vs. results", "", "| Target | Set before launch | Result |", "|---|---|---|",
              f"| Paying customers | {t['target_paying_customers']} (stop below {t['stop_below_paying_customers']}) | {m['paying_customers']} |",
              f"| Refund rate | at most {t['max_refund_rate']:.0%} | {_pct(m['refund_rate'])} |",
              f"| Store visitors to judge | {t['min_store_visitors']} | {m['store_visitors']} |",
              f"| Review after | {t['review_after_days']} days | {m['days_open']} days |", "",
              "## Analyst's memo (gpt-oss)", "", f"**{memo['headline']}**", "", memo["what_happened"], ""]
    if memo["assumptions"]:
        lines += ["### Riskiest assumptions", "", "| Assumption | Verdict | Evidence |", "|---|---|---|"]
        lines += [f"| {a['assumption']} | {a['verdict'].upper()} | {a['evidence']} |" for a in memo["assumptions"]]
        lines.append("")
    for title, key in (("What we learned", "learnings"), ("Support themes", "support_themes"),
                       ("Next steps", "next_steps"), ("Wind-down", "wind_down")):
        if memo[key]:
            lines += [f"### {title}", ""] + [f"- {x}" for x in memo[key]] + [""]
    if memo["pivot_options"]:
        lines += ["### Pivot options", ""] + [f"- **{p['change']}:** {p['option']} (evidence: {p['evidence']})"
                                               for p in memo["pivot_options"]] + [""]
    e = memo["next_experiment"]
    if e["hypothesis"]:
        lines += ["### Next experiment", "", f"- Hypothesis: {e['hypothesis']}", f"- Metric: {e['metric']}",
                  f"- Target: {e['target']}", ""]
    lines += ["Product usage is not measured (data stays in the user's browser, by design). Payments are MOCKED."]
    return "\n".join(lines) + "\n"


def render_gate_md(gate, locked, decision) -> str:
    return "\n".join(["# Phase 6 gate decision: persevere, pivot or stop", "", f"**Outcome:** {gate.outcome.upper()}", "",
                      f"**Founder decision:** {decision}", "", "## Reasons", ""] + [f"- {r}" for r in gate.reasons] +
                     ["", f"Targets locked at {locked['locked_at']} (SHA-256 `{locked['sha256']}`).", "",
                      "## Rule", "", "PERSEVERE when paying customers reach the target with refunds under the limit. "
                      "REPEAT while the review window is open or traffic is too low to judge. Otherwise PIVOT if paying "
                      "customers are at or above the stop line, else STOP. The founder may choose a stricter path, "
                      "never a looser one."]) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 6: Operate and learn")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("targets", "metrics", "review"):
        sub.add_parser(name)
    ss = sub.add_parser("support")
    ss.add_argument("--email")
    ss.add_argument("--message")
    args = p.parse_args(argv)
    try:
        if args.cmd == "targets":
            OperatePipeline().set_targets()
        elif args.cmd == "metrics":
            OperatePipeline().write_metrics()
        else:
            llm = LLMClient()
            print(f"Model: {llm.model} at {llm.base_url}")
            if args.cmd == "support":
                OperatePipeline(llm).support_reply(args.email, args.message)
            else:
                OperatePipeline(llm).review()
    except (FileNotFoundError, TargetsTampered, LLMError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Nothing was published, sent or spent.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
