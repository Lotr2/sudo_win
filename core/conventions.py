"""Shared conventions from CLAUDE.md: phases, Core steps, step catalogue, output naming, labels.

Every variant writes exactly these output files; only HOW they are produced may differ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MOCKED = "MOCKED"
STUBBED = "STUBBED"
ASSUMPTION = "ASSUMPTION"

GATE_OUTCOMES = ("go", "repeat", "pivot", "stop")
OUTPUT_EXTS = (".md", ".json", ".csv", ".txt", ".html")
MAX_OUTPUT_BYTES = 50_000
PAYMENT_TEST_LOG = "payment_test_log.md"
LOG_KEYS = ("timestamp", "agent", "action", "sources", "model", "cost", "approved_by")

PHASES = {
    1: ("Discover", "Find opportunities"),
    2: ("Validate", "Design the business"),
    3: ("Build", "Build the product"),
    4: ("Commercial setup", "Set the price and policies"),
    5: ("Launch", "Open the store; deliver automatically"),
    6: ("Operate and learn", None),
}

# Gate types: T = pre-set evidence threshold, X = checks/tests, F = founder approval.
@dataclass(frozen=True)
class Step:
    phase: int
    num: int
    short: str
    kind: str                  # "llm" or "code"
    title: str
    task: str = ""             # instruction given to the LLM role for this step
    ext: str = "md"
    gate: str = "X"
    outward: str | None = None   # outward action kind requiring founder approval
    sources_query: str | None = None  # query template; {idea} and {market} are filled from the brief
    reads: tuple[str, ...] = field(default_factory=tuple)  # default inputs (step keys)

    @property
    def key(self) -> str:
        return f"phase{self.phase}_step{self.num}"

    @property
    def action_id(self) -> str | None:
        return f"phase{self.phase}.step{self.num}.{self.outward}" if self.outward else None

    def filename(self, idea: str) -> str:
        return f"phase{self.phase}_step{self.num}_{idea}-{self.short}.{self.ext}"


STEPS: tuple[Step, ...] = (
    # Phase 1 Discover
    Step(1, 1, "brief", "llm", "Founder brief",
         "Restate the founder brief as a structured summary: idea, skills, budget, time, target market, values, constraints."),
    Step(1, 2, "opportunities", "llm", "Find opportunities",
         "List concrete customer pains and opportunities for this idea. Every claim must cite one of the SOURCES URLs or set source_url to null (it will be labelled ASSUMPTION).",
         gate="T", sources_query="{idea} problem", reads=("phase1_step1",)),
    Step(1, 3, "alternatives", "llm", "Score against alternatives",
         "Propose 2-3 alternative framings of the idea and score each 1-5 on fit with the founder's skills, budget, time and values.",
         reads=("phase1_step1", "phase1_step2")),
    Step(1, 4, "market", "llm", "Define the market",
         "Define the target segment, competitors or substitutes and their prices. Cite SOURCES URLs or set source_url to null.",
         gate="T", sources_query="{idea} alternatives pricing", reads=("phase1_step1", "phase1_step2")),
    Step(1, 5, "interview-kit", "llm", "Customer interviews (Mom Test)",
         "Write a Mom Test interview script (past behaviour, no pitching) and a short outreach draft the founder can send in their own name.",
         outward="send_outreach", reads=("phase1_step1", "phase1_step4")),
    # Phase 2 Validate
    Step(2, 1, "lean-canvas", "llm", "Lean Canvas",
         "Fill a Lean Canvas (problem, segments, UVP, solution, channels, revenue, costs, metrics, unfair advantage). Every claim cites SOURCES or is null.",
         sources_query="{market} demand", reads=("phase1_step2", "phase1_step3", "phase1_step4")),
    Step(2, 2, "landing-page", "llm", "Demand-test landing page",
         "Write landing-page copy for a waitlist demand test: headline, subheadline, 3 bullets, call to action. No testimonials, no urgency, no income claims.",
         ext="html", outward="publish_landing_page", reads=("phase2_step1",)),
    Step(2, 3, "demand-test", "llm", "Demand test plan",
         "Set the demand-test target BEFORE any results (e.g. waitlist sign-ups in N days) and describe how results are measured. Do not invent results.",
         reads=("phase2_step1", "phase2_step2")),
    Step(2, 4, "evidence-scorecard", "code", "Evidence scorecard", ext="json", gate="T"),
    # Phase 3 Build
    Step(3, 1, "mvp-spec", "llm", "MVP spec",
         "Specify the smallest digital MVP: list of features (short names) and acceptance criteria.",
         reads=("phase2_step1", "phase1_step1")),
    Step(3, 2, "mvp", "llm", "MVP",
         "Produce the MVP content itself. Use one section per feature, with the feature name in the heading.",
         reads=("phase3_step1",)),
    Step(3, 3, "user-docs", "llm", "User docs",
         "Write user documentation: getting started and one section per feature.",
         reads=("phase3_step1", "phase3_step2")),
    Step(3, 4, "test-report", "code", "MVP tests"),
    # Phase 4 Commercial setup (no payments of any kind)
    Step(4, 1, "pricing", "llm", "Set the price",
         "Recommend one price in USD (price_usd) with reasoning from competitor prices in the inputs. Cite SOURCES or null.",
         gate="T", reads=("phase1_step4", "phase2_step1", "phase3_step1")),
    Step(4, 2, "terms", "code", "Terms (draft)"),
    Step(4, 3, "privacy", "code", "Privacy policy (draft)"),
    Step(4, 4, "refund-policy", "code", "Refund policy (draft)"),
    # Phase 5 Launch (all MOCKED)
    Step(5, 1, "store-page", "llm", "Store page",
         "Write store-page copy: headline, subheadline, 3 bullets, call to action. No testimonials, no urgency, no income claims.",
         ext="html", outward="open_store", reads=("phase3_step1", "phase4_step1")),
    Step(5, 2, "launch-posts", "llm", "Launch posts",
         "Draft 2-3 launch posts for relevant communities, written for the founder to post in their own name. No hype, no income claims.",
         outward="publish_posts", reads=("phase2_step1", "phase4_step1")),
    Step(5, 3, "delivery-test", "code", "Automatic delivery test"),
    Step(5, 4, "refund-test", "code", "Refund test"),
    Step(5, 5, "reconciliation", "code", "Reconciliation", ext="csv"),
    # Phase 6 Operate and learn
    Step(6, 1, "support", "llm", "Support playbook",
         "Write a support FAQ and reply templates based on the user docs. Replies are drafts for the founder.",
         outward="send_support_replies", reads=("phase3_step3",)),
    Step(6, 2, "metrics", "code", "Metrics"),
    Step(6, 3, "persevere-pivot-memo", "llm", "Persevere-or-pivot memo",
         "Write a persevere-or-pivot memo from the gate results and metrics. Say plainly which evidence is missing. Do not invent customer feedback.",
         reads=("phase2_step4", "phase5_step5", "phase6_step2")),
)

STEP_BY_KEY = {s.key: s for s in STEPS}
LLM_STEP_KEYS = tuple(s.key for s in STEPS if s.kind == "llm")


def steps_in_phase(phase: int) -> list[Step]:
    return [s for s in STEPS if s.phase == phase]


def gate_filename(phase: int, idea: str, outcome: str) -> str:
    assert outcome in GATE_OUTCOMES, outcome
    return f"phase{phase}_gate_{idea}-{outcome}.md"


IDEA_RE = r"[a-z0-9]+"
STEP_FILE_RE = re.compile(rf"^phase([1-6])_step(\d+)_({IDEA_RE})-([a-z0-9-]+)\.(md|json|csv|txt|html)$")
GATE_FILE_RE = re.compile(rf"^phase([1-6])_gate_({IDEA_RE})-(go|repeat|pivot|stop)\.md$")


def is_valid_output_name(name: str) -> bool:
    """True if `name` is a catalogue step file, a gate file, or the payment test log."""
    if name == PAYMENT_TEST_LOG:
        return True
    if GATE_FILE_RE.match(name):
        return True
    m = STEP_FILE_RE.match(name)
    if not m:
        return False
    phase, num, _idea, short, ext = m.groups()
    step = STEP_BY_KEY.get(f"phase{phase}_step{num}")
    return bool(step and step.short == short and step.ext == ext)
