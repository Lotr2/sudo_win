"""Baseline variant: hub-and-spoke. One LLM call per LLM step, shared code steps, one gate per phase.

Copy this whole folder to variants/<name>/ and change HOW it works (roles, order, prompts, models,
verifier, retries, context). Do not write files, call the network or touch approvals directly:
use the BaseVariant helpers (llm_step, code_step, phase_gate, verify, digest).
"""
from core.base_variant import BaseVariant, RunResult


class Variant(BaseVariant):
    def run(self, brief, decisions) -> RunResult:
        max_repeats = int(self.config["max_phase_repeats"])
        decision = None
        for phase in range(1, 7):
            for _attempt in range(max_repeats + 1):
                for step in self.steps(phase):
                    if step.kind == "llm":
                        self.llm_step(step.key)
                    else:
                        self.code_step(step.key)
                decision = self.phase_gate(phase)
                if decision.final != "repeat" or phase == 6:
                    break
            if decision.final == "go":
                continue
            if decision.final == "repeat":
                if phase == 6:
                    return self.result("completed", 6, "repeat")
                return self.result("stopped", phase, "repeat", notes=["repeat limit reached"])
            if decision.final == "pivot":
                return self.result("pivot", phase, "pivot", next_brief=self.pivot_target())
            return self.result("stopped", phase, "stop")
        return self.result("completed", 6, decision.final)
