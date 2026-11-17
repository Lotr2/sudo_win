"""anan: tuned baseline for gpt-oss-20b.

Same hub-and-spoke waves and stage review as the template. Differences:
- narrower inputs per step (agents' READS), which also lets more steps share a wave and run in parallel;
- honest context hints (HINTS) on two steps where a small model is most tempted to invent things (price
  anchoring, customer evidence). The core adds them to every attempt, including revisions, without costing
  parallelism; the demand-test rule lives in the analyst prompt.
"""
from core.base_variant import BaseVariant, RunResult


class Variant(BaseVariant):
    HINTS = {
        "phase4_step1": ("\nPRICE RULE: base price_usd on competitor_prices_usd from the market step. If there are none, "
                         "add a claim saying the price is a guess, ending in [ASSUMPTION]."),
        "phase6_step3": ("\nCUSTOMER EVIDENCE: there are no real customers yet; every order so far is a MOCKED test order. "
                         "List the evidence to collect next in evidence_gaps."),
    }

    def run(self, brief, decisions) -> RunResult:
        decision = None
        for phase in range(1, 7):
            decision = self.review_phase(phase)
            if decision.final == "go":
                continue
            if decision.final == "repeat":          # gate 6: collect real customer evidence before going on
                return self.result("completed", phase, "repeat")
            if decision.final == "pivot":
                return self.result("pivot", phase, "pivot", next_brief=self.pivot_target())
            return self.result("stopped", phase, decision.final)
        return self.result("completed", 6, decision.final)
