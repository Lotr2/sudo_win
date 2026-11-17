"""Fake model client, search and fetch for tests: schema-valid fixtures, no network, no model."""

from __future__ import annotations

import json
import re

WORDS = ("This section explains the problem in plain words, shows a worked example, and lists the steps a busy "
         "freelancer can follow today. It covers what to prepare, what to check, and how to avoid the common "
         "mistakes people make when they start. Each step is short and concrete so it can be done in one sitting. ")


def _payload(user: str) -> dict:
    try:
        return json.loads(user.split("\n\nYour previous reply")[0])
    except json.JSONDecodeError:
        return {}


class FakeModels:
    """Responder for FakeClient. Attributes let a test change one behaviour."""

    def __init__(self):
        self.profile = {"idea": "Invoice templates and a late-payment guide for freelance designers",
                        "skills": ["design", "writing"], "budget_usd": 200, "hours_per_week": 8,
                        "market": "freelance designers", "values": ["honest pricing"], "gaps": []}
        self.block_marker = "BLOCKME"
        self.variants = None
        self.cite_unknown = False
        self.calls: list[str] = []

    def __call__(self, *, role, model, system, user, schema):
        self.calls.append(role)
        p = _payload(user)
        fn = getattr(self, role)
        return fn(p)

    # ---- roles ----
    def brief_parser(self, p):
        return dict(self.profile)

    def founder_liaison(self, p):
        task = p.get("task")
        if task == "intake_question":
            return {"question": f"What is your {p['field']}?", "why_it_matters": "it changes which idea fits"}
        if task == "extract_updates":
            ans, q = p["answer"], p["question"]
            updates = []
            if "$" in ans or "budget" in ans.lower() or "spend" in ans.lower():
                updates.append({"field": "budget_usd", "value": ans})
            elif "hour" in ans.lower():
                updates.append({"field": "hours_per_week", "value": ans})
            else:
                m = re.search(r"about (\w+)", q)
                if m and m.group(1) in {"idea", "skills", "market", "values", "budget_usd", "hours_per_week"}:
                    updates.append({"field": m.group(1), "value": ans})
            return {"updates": updates, "is_question": False}
        if task == "read_back":
            return {"summary": "You want to sell templates to freelance designers.", "open_assumptions": p["open_assumptions"]}
        if task == "ask_anything":
            q = set(re.findall(r"[a-z]{4,}", p["question"].lower()))
            for rid, text in p["record"].items():
                if q & set(re.findall(r"[a-z]{4,}", text.lower())):
                    return {"answer": f"Because of {rid}.", "cited_ids": [rid]}
            return {"answer": "I think it was the market size.", "cited_ids": []}
        return {"message": f"[{task}] Here is what happens next. Anything I should know before I start?"}

    def opportunity_scout(self, p):
        return {"variants": self.variants or [
            {"id": "v1", "name": "Late-payment guide", "target_customer": "freelance designers", "problem": "clients pay late",
             "product_form": "guide", "skills_needed": ["writing"], "est_build_hours": 10, "budget_needed_usd": 0},
            {"id": "v2", "name": "Invoice template pack", "target_customer": "freelance designers", "problem": "messy invoices",
             "product_form": "template_pack", "skills_needed": ["design"], "est_build_hours": 12, "budget_needed_usd": 20},
            {"id": "v3", "name": "Rate calculator", "target_customer": "new freelancers", "problem": "underpricing",
             "product_form": "web_tool", "skills_needed": ["javascript"], "est_build_hours": 60, "budget_needed_usd": 500}]}

    def _plan(self, p):
        return {"queries": [f"{p['variant']['name']} problem"]}

    def _findings(self, p, kinds):
        ids = [e["id"] for e in p["evidence"]]
        if self.cite_unknown:
            return {"findings": [{"kind": kinds[0], "claim": "made up", "evidence_ids": ["ev_999"]}]}
        out = []
        for i, eid in enumerate(ids):
            quote = next(e["quote"] for e in p["evidence"] if e["id"] == eid)
            out.append({"kind": kinds[i % len(kinds)], "claim": quote[:120], "evidence_ids": [eid]})
        return {"findings": out}

    def market_researcher(self, p):
        return self._plan(p) if p["task"] == "plan" else self._findings(p, ["pain", "pain", "pain", "channel"])

    def competitor_analyst(self, p):
        return self._plan(p) if p["task"] == "plan" else self._findings(p, ["complaint", "price", "alternative"])

    def mom_test_writer(self, p):
        return {"intro": "I'm learning how freelancers handle invoices.", "questions": ["Tell me about the last late payment."],
                "notes_template": "- Date:\n- Who:\n- Problem in their words:"}

    def gate_reporter(self, p):
        return {"summary": f"Gate {p['gate']}: {p['result']['outcome']}.", "explanation": "Explained from the numbers.",
                "what_would_change_it": ["More sourced evidence"],
                "other_directions": [
                    {"kind": "customer group", "suggestion": "Sell the same kit to freelance writers.",
                     "why": "They send invoices too and use the same skills."},
                    {"kind": "product idea", "suggestion": "A one-page late-payment checklist.",
                     "why": "Smaller to build and tests the same need."}]}

    def canvas_builder(self, p):
        eid = [e["id"] for e in p.get("evidence", [])][:1]
        box = lambda t: {"text": t, "evidence_ids": eid, "assumption": not eid}
        return {k: box(f"{k} text") for k in ["problem", "customer_segments", "unique_value", "solution", "channels",
                                              "revenue", "costs", "key_metrics", "unfair_advantage"]}

    def skeptical_investor(self, p):
        return {"scores": [{"box": b, "score": 3, "comment": "ok"} for b in p["boxes"]],
                "risks": [{"assumption": "designers will pay", "severity": 4, "fatal": False}],
                "objections": ["Why would they pay instead of using a free template?"]}

    def experiment_designer(self, p):
        return {"kind": "waitlist", "description": "Share the waitlist page in two communities.", "metric": "waitlist sign-ups",
                "target": 100, "window_days": 14, "rationale": "Enough to show real pull."}

    def landing_copywriter(self, p):
        return {"headline": "Get paid on time", "subheadline": "Templates and a guide for freelance designers",
                "benefits": ["Clear invoices", "Polite reminders"], "cta_text": "Join the waitlist", "body": "Built by a designer."}

    def product_manager(self, p):
        form = p["product_form"]
        base = {"title": "Get Paid Kit", "summary": "A kit", "product_form": form, "features": ["templates", "guide"]}
        if form == "guide":
            base["sections"] = [{"heading": h, "purpose": "x"} for h in ["Start here", "Invoices", "Reminders", "Late fees"]]
            base["min_words_per_section"] = 40
        elif form == "template_pack":
            base |= {"item_count": 4, "item_fields": ["When to use", "Template"]}
        else:
            base["inputs"] = [{"id": "hours", "label": "Hours"}, {"id": "rate", "label": "Rate"}]
        return base

    def builder(self, p):
        form, spec = p["product_form"], p["spec"]
        if form == "guide":
            return {"title": spec["title"], "sections": [{"heading": s["heading"], "body": WORDS} for s in spec["sections"]]}
        if form == "template_pack":
            return {"title": spec["title"], "items": [{"name": f"Template {i}", "fields": [
                {"heading": f, "body": WORDS} for f in spec["item_fields"]]} for i in range(spec["item_count"])]}
        return {"title": spec["title"], "description": "Calculates a rate.", "result_label": "Result",
                "compute_js": "      return 'Total: ' + (v.hours * v.rate);"}

    def docs_writer(self, p):
        return {"user_guide": "## Start\n\nOpen the kit and follow the guide step by step to send your first invoice.\n\n"
                              "## Access\n\nYou get a download link right after purchase.",
                "faq": [{"q": "How do I get access?", "a": "You get a download link after purchase."},
                        {"q": "Can I get a refund?", "a": "Yes, within the refund window in the refund policy."}]}

    def pricing_analyst(self, p):
        ids = [e["id"] for e in p.get("price_evidence", [])][:1]
        return {"currency": "USD", "price": 19, "tiers": [], "reasoning": "Similar kits sell nearby.", "evidence_ids": ids}

    def policy_drafter(self, p):
        days = p["refund_window_days"]
        return {"terms": "DRAFT: not legal advice\n\nLicence: personal use. Refunds: see the refund policy. Contact: email.",
                "privacy": "DRAFT: not legal advice\n\nWe collect your email and use it for delivery. Passwords and card "
                           "numbers are never collected or stored. Contact: email.",
                "refunds": f"DRAFT: not legal advice\n\nYou can request a refund within {days} days by email.",
                "refund_window_days": days}

    def launch_writers(self, p):
        return {"store_page": {"headline": "Get Paid Kit", "description": "Templates and a guide.", "whats_included": ["4 templates"],
                               "price_line": "$19", "refund_line": "Refunds within 14 days."},
                "posts": [{"channel": c, "text": f"I made a kit for designers who chase invoices. ({c})"} for c in p["channels"]]}

    def support_agent(self, p):
        ids = [x["id"] for x in p["doc_passages"]][:1]
        refund = "refund" in p["ticket"].lower()
        return {"answer": "The founder will review your request." if refund else "You get a download link after purchase.",
                "cited_doc_ids": ids, "escalate": False, "is_refund_request": refund}

    def pivot_memo_writer(self, p):
        keys = [k for k in p["metrics"] if k in ("conversion", "refund_rate")]
        return {"recommendation": "persevere", "body": "MOCKED data: conversion meets the target.",
                "claims": [{"text": "Conversion meets the target", "metric_keys": keys[:1]}]}

    def critic(self, p):
        return {"checks": [{"claim_index": i, "matches_data": True, "comment": "ok"} for i, _ in enumerate(p["memo"]["claims"])],
                "rubric_score": 4, "gaps": []}

    def policy_classifier(self, p):
        bad = self.block_marker in p["text"]
        return {"allowed": not bad, "category": "harmful" if bad else "ok", "reason": "test"}


class FakeSearch:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.queries: list[str] = []

    def __call__(self, query: str, limit: int = 5):
        from venture_builder.tools.search import SearchUnavailable
        self.queries.append(query)
        if self.fail:
            raise SearchUnavailable("search down (test)")
        n = len(self.queries)
        return [{"url": f"https://forum{i}.example.org/t/{n}", "title": "t",
                 "content": f"Freelancers say clients pay invoices 30 days late (thread {n}-{i})."} for i in range(3)]


def fake_fetch(url: str) -> str:
    return ""
