"""Plain-English names for stages, steps and gate outcomes, shared by the web UI (through /api/info) and the CLI."""

from __future__ import annotations

import re

PHASES = {
    1: ("Explore the idea", "Discover"),
    2: ("Test demand", "Validate"),
    3: ("Build the product", "Build"),
    4: ("Price and policies", "Commercial setup"),
    5: ("Launch and sell", "Launch"),
    6: ("Learn and improve", "Operate"),
}

OUTCOMES = {
    "go": "Go ahead", "persevere": "Keep going", "repeat": "Try again", "pivot": "Change direction", "stop": "Stop here",
}

OPTION_HELP = {
    "go": "Move on to the next stage.",
    "repeat": "Redo this stage, for example with more research or a new test.",
    "pivot": "Switch to a different version of the idea.",
    "stop": "End the project here. Sensible when the evidence is weak.",
}

STATUS = {"running": "In progress", "finished": "Finished", "stopped": "Stopped", "failed": "Needs attention"}

STEPS = {
    "p1_parse": "Reading your idea",
    "p1_intake_ask": "Preparing questions for you",
    "p1_readback": "Summarising what we understood",
    "p1_policy": "Checking the idea is something we can help with",
    "p1_scout": "Coming up with versions of the idea",
    "p1_research": "Researching the market and competitors",
    "p1_score": "Scoring each version against the evidence",
    "p1_gate": "Weighing the evidence for stage 1",
    "p1_mom_test": "Writing customer interview questions",
    "p2_start": "Starting stage 2: testing demand",
    "p2_canvas": "Drafting the business plan on one page",
    "p2_review": "A sceptical reviewer is checking the plan",
    "p2_experiment": "Designing the demand test",
    "p2_lock": "Fixing the success target before any results come in",
    "p2_landing": "Writing the landing page",
    "p2_publish": "Preparing the landing page for your approval",
    "p2_gate": "Weighing the evidence for stage 2",
    "p3_start": "Starting stage 3: building",
    "p3_spec": "Writing the product plan",
    "p3_build": "Building the product and testing it",
    "p3_docs": "Writing the user guide",
    "p3_gate": "Checking the product is ready",
    "p4_start": "Starting stage 4: price and policies",
    "p4_pricing": "Working out a fair price from competitor prices",
    "p4_policies": "Drafting refund and privacy policies",
    "p4_gate": "Checking pricing and policies",
    "p5_start": "Starting stage 5: launch (practice mode)",
    "p5_channels": "Choosing where to tell people about it",
    "p5_drafts": "Drafting the store page and posts",
    "p5_publish": "Waiting for your approvals",
    "p5_sale": "Running a practice sale",
    "p5_sale_refund": "Running a practice refund",
    "p5_gate": "Checking the launch",
    "p6_start": "Starting stage 6: learn and improve",
    "p6_events": "Collecting results",
    "p6_support": "Answering customer questions",
    "p6_memo": "Writing the review of results",
    "p6_gate": "Deciding whether to keep going or change course",
}


def phase_name(n: int) -> str:
    return PHASES.get(int(n), (f"Stage {n}", ""))[0]


def outcome_name(label: str, gate: int | None = None) -> str:
    if label == "go" and gate == 6:
        return OUTCOMES["persevere"]
    return OUTCOMES.get(label, label.replace("_", " ").capitalize())


def human(name: str) -> str:
    s = str(name).replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:]


def as_dict() -> dict:
    return {"phases": {str(k): v[0] for k, v in PHASES.items()}, "outcomes": OUTCOMES, "option_help": OPTION_HELP,
            "status": STATUS, "steps": STEPS, "fields": FIELDS}


# ---- progress events in plain words ----

_NOTES = [
    (r"^Phase (\d) · (.*) started(.*)$", lambda m: None),  # the stage's start step already says this
    (r"^Opportunity Scout proposed (\d+) variants: (.*)$", lambda m: f"The team came up with {m[1]} versions of the idea: {m[2]}"),
    (r"^Build loop (\d+): (\d+)/(\d+) acceptance tests pass.*$", lambda m: f"Build attempt {m[1]}: {m[2]} of {m[3]} quality checks pass"),
    (r"^Compliance Linter pass (\d+): (\d+) violation\(s\)$",
     lambda m: f"Checking the wording for honesty and legal red flags: {'all clear' if m[2] == '0' else m[2] + ' issue(s) to fix'}"),
    (r"^Search failed: .*$", lambda m: "Web search isn't responding, so some findings are marked as guesses (ASSUMPTION) for now"),
    (r"^Correction affects scoring.*$", lambda m: "Thanks for the correction: re-scoring with it (the research stays as it is)"),
    (r"^Demo time budget.*$", lambda m: "Demo mode time limit reached for this step"),
    (r"^Demo mode: interview-notes pause skipped.*$", lambda m: "Demo mode: skipping customer interviews; customer findings stay guesses"),
]


def doc_label(name: str) -> str:
    """phase1_step2_scores.md -> 'Stage 1: Scores'; phase1_gate_decision.md -> 'Stage 1: Gate report'."""
    m = re.match(r"^phase(\d)_(?:step\d+_|gate_)?(.*)\.(\w+)$", name or "")
    if not m:
        return name
    return f"Stage {m[1]}: " + ("Gate report" if "_gate_" in name else human(m[2])) + (" (web page)" if m[3] == "html" else "")


def event_text(row: dict) -> str | None:
    """One progress event in plain words, or None when it isn't worth showing to the founder."""
    kind = row.get("kind")
    if kind == "step":
        return STEPS.get(row.get("node", ""))
    if kind == "output":
        return "New document: " + doc_label(row.get("file", ""))
    if kind == "error":
        return "This step hit a problem. Your progress is saved, and you can try again."
    if kind == "chat":
        return None
    msg = row.get("message", "")
    if msg.startswith("run ") and " created" in msg:
        return "Project created"
    for pattern, fmt in _NOTES:
        m = re.match(pattern, msg)
        if m:
            return fmt(m)
    return msg


# ---- profile fields in plain words ----

FIELDS = {
    "idea": "idea", "skills": "skills", "budget_usd": "budget", "hours_per_week": "weekly hours",
    "market": "target customers", "values": "values",
}


def field_name(field: str) -> str:
    return FIELDS.get(field, str(field).replace("_", " "))


def plural(n, word: str, many: str | None = None) -> str:
    """plural(1, 'website') -> '1 website'; plural(3, 'website') -> '3 websites'."""
    return f"{n:g} {word if float(n) == 1 else (many or word + 's')}"


def field_value(field: str, value) -> str:
    """A profile value as a person would write it: $10,000, 40 hours a week, a, b and c."""
    if isinstance(value, str) and value.strip().startswith("[") and value.strip().endswith("]"):
        import ast
        try:
            value = ast.literal_eval(value.strip())
        except (ValueError, SyntaxError):
            pass
    if value in (None, "", []):
        return "not given yet"
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    if field == "budget_usd":
        try:
            return f"${float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)
    if field == "hours_per_week":
        try:
            return f"{float(value):g} hours a week"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


# ---- numbers behind a stage check, in plain words ----

DETAIL_NAMES = {
    "best_version": "Best version of the idea", "passing_version": "Version that passes",
    "score": "Score (out of 100)", "pain_signals": "Real examples of people with this problem",
    "pain_domains": "Different websites those examples came from", "fit_rules_pass": "Fits your skills, budget and time",
    "target": "Goal set in advance", "result": "Result", "ratio": "Share of the goal reached", "metric": "What was measured",
    "fatal_risks": "Risks that could sink the business", "passed": "Quality checks passed", "total": "Quality checks in total",
    "loops": "Build rounds", "violations": "Wording problems to fix", "price": "Price", "sourced_range":
    "What similar products charge", "published": "Items approved and recorded", "rejected": "Items you asked to change",
    "pending": "Items waiting for you", "delivery_tests_passed": "Practice sale, delivery and refund worked",
    "metrics": "Results", "targets": "Goals", "misses": "Goals missed", "conversion": "Visitors who bought",
    "refund_rate": "Buyers who asked for a refund", "min_conversion": "Lowest acceptable share of visitors who buy",
    "max_refund_rate": "Highest acceptable refund share", "visitors": "Visitors", "orders": "Orders", "refunds": "Refunds",
}


def detail_name(key: str) -> str:
    return DETAIL_NAMES.get(key, human(key))


def detail_value(key: str, value):
    """Format one gate input for people: 0.47 score -> 47; ratios and rates -> percentages."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        if key == "score":
            return f"{float(value) * 100:.0f}"
        if key in {"ratio", "conversion", "refund_rate", "min_conversion", "max_refund_rate"}:
            return f"{float(value):.0%}"
        if key == "price":
            return f"${float(value):g}"
        if key == "sourced_range" and isinstance(value, (list, tuple)) and len(value) == 2:
            return f"${float(value[0]):g} to ${float(value[1]):g}"
    except (TypeError, ValueError):
        pass
    return value
