"""Pricing analyst agent: proposes one launch plan from the evidence gathered so far."""

from __future__ import annotations

import pricing

from .base import as_json, format_brief, load_prompt


class PricingAnalyst:
    name = "pricing_analyst"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("pricing_analyst")

    def propose(self, handoff: dict, feedback: str | None = None, previous: dict | None = None):
        spec = handoff.get("spec") or {}
        canvas = handoff.get("canvas") or {}
        user = (f"## Founder brief\n{format_brief(handoff.get('brief') or {})}\n\n"
                f"## Lean Canvas (revenue, customers, alternatives, costs)\n"
                f"{as_json({k: canvas.get(k) for k in ('customer_segments', 'early_adopters', 'existing_alternatives', 'unique_value_proposition', 'revenue_streams', 'cost_structure')})}\n\n"
                f"## Demand-test evidence\n{as_json(handoff.get('evidence') or {})}\n\n"
                f"## MVP (what is actually built)\n{as_json({'product_name': spec.get('product_name'), 'summary': spec.get('summary'), 'stories': [s.get('story') for s in spec.get('stories') or []], 'out_of_scope': spec.get('out_of_scope')})}\n")
        if previous:
            user += f"\n## Your previous proposal\n{as_json(previous)}\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"
        user += "\nPropose the launch price. Reply with the JSON object."
        result = self.llm.chat_json(self.system, user, required_keys=("billing", "price"))
        return pricing.normalise(result.data), result
