"""Baseline variant: hub-and-spoke. Each phase runs in waves (independent steps in parallel), then a stage review.

Stage review (review_phase): the gate recommends; the founder chooses go / pivot / stop, or revise / redo
(gates 1-4) / reset (gate 5); retries re-run and re-check the gate until it is decided, up to max_phase_retries.

Copy this whole folder to variants/<name>/ and change HOW it works (roles, order via run_phase, prompts,
models, sampling, verifier, retries, budgets, HINTS). Do not write files, call the network, start threads or
touch approvals directly: use the BaseVariant helpers.
"""
from core.base_variant import BaseVariant, RunResult


class Variant(BaseVariant):
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
