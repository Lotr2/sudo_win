"""anan: tuned baseline for gpt-oss-20b.

Same hub-and-spoke waves and gates as the template. Differences:
- narrower inputs per step (agents' READS), which also lets more steps share a wave and run in parallel;
- honest context hints on two steps where a small model is most tempted to invent things (price anchoring,
  customer evidence). Both run alone in their wave, so the hint costs no parallelism; the demand-test rule
  lives in the analyst prompt because that step shares a wave with the landing page.
"""
from core.base_variant import BaseVariant, RunResult

HINTS = {
    "phase4_step1": ("\nPRICE RULE: base price_usd on competitor_prices_usd from the market step. If there are none, "
                     "add a claim saying the price is a guess, ending in [ASSUMPTION]."),
    "phase6_step3": ("\nCUSTOMER EVIDENCE: there are no real customers yet; every order so far is a MOCKED test order. "
                     "List the evidence to collect next in evidence_gaps."),
}


class Variant(BaseVariant):
    def _run_wave(self, keys: list[str]) -> None:
        for k in [k for k in keys if k in HINTS]:
            self.llm_step(k, extra_inputs=HINTS[k])
        rest = [k for k in keys if k not in HINTS]
        if rest:
            self.parallel(rest)

    def run(self, brief, decisions) -> RunResult:
        max_repeats = int(self.config["max_phase_repeats"])
        decision = None
        for phase in range(1, 7):
            for attempt in range(max_repeats + 1):
                if attempt:
                    self.reset_phase(phase)
                for wave in self.waves(phase):
                    self._run_wave([s.key for s in wave])
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
