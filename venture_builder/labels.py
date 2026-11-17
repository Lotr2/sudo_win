"""Plain-English names for stages, steps and gate outcomes, shared by the web UI (through /api/info) and the CLI."""

from __future__ import annotations

import math
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
    """'go' -> 'Go ahead'; 'go (MOCKED evidence)' -> 'Go ahead (practice data)'."""
    base = str(label).split(" (")[0].strip().lower()
    name = OUTCOMES["persevere"] if base == "go" and gate == 6 else OUTCOMES.get(base, base.replace("_", " ").capitalize())
    return name + (" (practice data)" if "mocked" in str(label).lower() else "")


def human(name: str) -> str:
    s = str(name).replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:]


def as_dict() -> dict:
    return {"phases": {str(k): v[0] for k, v in PHASES.items()}, "outcomes": OUTCOMES, "option_help": OPTION_HELP,
            "status": STATUS, "steps": STEPS, "fields": FIELDS}


# ---- progress events in plain words ----

_NOTES = [
    (r"^Phase (\d) · (.*) started(.*)$", lambda m: None),  # the stage's start step already says this
    (r"^The team considered (\d+) versions.*$", lambda m: m.group(0)),
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
    "support_tickets": "Support questions", "tickets_per_order": "Support questions per order",
}


def detail_name(key: str) -> str:
    return DETAIL_NAMES.get(key, human(key))


def points(score: float, need: float | None = None) -> str:
    """A 0-1 score as points out of 100, to one decimal (0.7 -> "70", 0.724 -> "72.4"). With a threshold, the
    shown number never lands on the other side of it: 0.5996 against 0.6 is "59.9", never "60"."""
    v, n = score * 100, None if need is None else need * 100
    shown = round(v, 1)
    if n is not None and (v < n) != (shown < n):
        shown = math.floor(v * 10) / 10 if v < n else math.ceil(v * 10) / 10
    return f"{shown:g}"


def detail_value(key: str, value):
    """Format one gate input for people: 0.47 score -> 47; ratios and rates -> percentages."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        if key == "score":
            return points(float(value))
        if key in {"conversion", "refund_rate"}:
            return f"{float(value):.1%}"
        if key in {"ratio", "min_conversion", "max_refund_rate"}:
            return f"{float(value):.0%}"
        if key == "price":
            return f"${float(value):g}"
        if key == "sourced_range" and isinstance(value, (list, tuple)) and len(value) == 2:
            return f"${float(value[0]):g} to ${float(value[1]):g}"
    except (TypeError, ValueError):
        pass
    return value


# ---- policy blocks in plain words ----

POLICY_AREAS = {
    "R11": "financial, investment or crypto products", "R12": "health or medical advice products",
    "R13": "physical goods", "fake_social_proof": "fake reviews, ratings or user counts",
    "fake_urgency": "fake urgency or countdowns", "income_promise": "promises of income",
    "impersonation": "posing as someone else", "money_movement": "moving money automatically",
    "credentials": "handling passwords, card numbers or other people's accounts",
    "excluded_product": "the kinds of products the team doesn't build",
}


def policy_reason(hit) -> str:
    """'health or medical advice products (your brief mentions "medical")' from a GuardResult."""
    area = POLICY_AREAS.get(hit.rule or "", POLICY_AREAS.get(hit.category, hit.category.replace("_", " ")))
    m = re.search(r": '([^']+)'", hit.reason or "")
    return area + (f" (your idea mentions “{m.group(1)}”)" if m else "")


DOCS = {"terms": "the terms of service", "privacy": "the privacy policy", "refunds": "the refund policy",
        "store_page": "the store page", "posts": "a launch post", "price": "the pricing"}
CATEGORY_WORDS = {"income_promise": "reads like a promise of results or income",
                  "fake_urgency": "creates fake urgency", "fake_social_proof": "suggests reviews or user numbers we don't have",
                  "impersonation": "speaks as someone else", "credentials": "asks for passwords or card details"}


def violation_text(v: str) -> str:
    """'refunds: matched rule R7 (income_promise): 'guarantees of results'' -> plain words."""
    m = re.match(r"^(\w+): matched rule \w+ \((\w+)\): '(.*)'$", v or "")
    if not m:
        m2 = re.match(r"^(\w+): (.*)$", v or "")
        return f"{DOCS.get(m2[1], m2[1])}: {m2[2]}" if m2 else str(v)
    return f"{DOCS.get(m[1], m[1])} says \u201c{m[3]}\u201d, which {CATEGORY_WORDS.get(m[2], m[2].replace('_', ' '))}"


# ---- gate reasons: the founder-facing sentence for each reason code a gate returns (gates.py decides) ----

def _version(v: dict) -> str:
    return f"\"{v['name']}\"" if v.get("name") else f"version {v['id']}"


def _shortfall(s: dict) -> str:
    if s["kind"] == "pain_signals":
        return f"only {plural(s['have'], 'real example')} of people with this problem (needs {s['need']})"
    if s["kind"] == "pain_domains":
        return f"examples from only {plural(s['have'], 'website')} (needs {s['need']})"
    if s["kind"] == "score":
        return f"a score of {points(s['have'], s['need'])} out of 100 (needs {points(s['need'])})"
    return "it doesn't fit your budget, time or skills well enough"


def metric_miss(m: dict) -> str:
    if m["kind"] == "conversion":
        return f"only {m['have']:.1%} of visitors bought (target {m['need']:.1%})"
    return f"{m['have']:.1%} of buyers asked for a refund (limit {m['limit']:.1%})"


def _launch_not_ready(f: dict) -> str:
    why = []
    if f["pending"]:
        why.append(f"{f['pending']} item(s) still waiting for your approval")
    if f["rejected"]:
        why.append(f"{f['rejected']} item(s) you asked to change need revising")
    if not f["delivery_tests_passed"]:
        why.append("the practice sale, delivery or refund checks didn't all pass")
    return ("; ".join(why) + ".").capitalize()


def _amount(f: dict, value: float) -> str:
    """A demand-test number: a share of visitors as a percentage ("1.2%", "8%"), a count as itself."""
    return f"{value:.1%}".replace(".0%", "%") if f.get("rate") else f"{value:g}"


GATE_REASONS = {
    # Gate 1
    "excluded_category": lambda f: (
        f"This idea falls under {f['category']}, which the team doesn't help with: these products can cause real harm "
        "if the advice is wrong, and they need licensed experts. Consider a related idea outside that area."),
    "search_failed": lambda f: (
        "Web search wasn't working, so the team couldn't check the market and everything is still a guess "
        "(ASSUMPTION). That says nothing bad about your idea: try again once search is back."),
    "no_versions": lambda f: "The team couldn't come up with versions of the idea to test.",
    "top_version_passes": lambda f: (
        f"The best version, {_version(f['top'])}, looks promising: "
        f"the team found {plural(f['top']['pain_signals'], 'real example')} of people struggling with this problem "
        f"on {plural(f['top']['pain_domains'], 'different website')}, it scores {points(f['top']['score'], f['min_score'])} out of "
        f"100 (needs {points(f['min_score'])}), and it fits your skills, budget and time."),
    "other_version_passes": lambda f: (
        f"The top-scoring version, {_version(f['top'])}, falls short, but {_version(f['other'])} passes: "
        f"{plural(f['other']['pain_signals'], 'real example')} of the problem on "
        f"{plural(f['other']['pain_domains'], 'website')}, scoring "
        f"{points(f['other']['score'], f['min_score'])} out of 100. Switching to it is the better bet."),
    "no_pain_found": lambda f: (
        "The team hasn't yet found real people online describing this problem, so the need is still a "
        "guess. Another round of research, ideally with a narrower customer group, may find it."),
    "no_pain_after_repeats": lambda f: (
        f"After {f['repeats']} extra rounds of research, the team still found no real people describing this "
        "problem. Without that, building it would be a gamble."),
    "below_bar": lambda f: (
        f"The best version so far, {_version(f['top'])}, isn't there yet: "
        + "; ".join(_shortfall(s) for s in f["shortfalls"]) + "."),
    "below_bar_after_repeats": lambda f: (
        f"Even after {f['repeats']} extra rounds, the best version, {_version(f['top'])}, falls short: "
        + "; ".join(_shortfall(s) for s in f["shortfalls"]) + "."),
    # Gate 2
    "fatal_risk_unanswered": lambda f: (
        f"There's a risk that could sink the business, and it hasn't been answered yet: {f['risk']}"),
    "target_met": lambda f: (
        f"The demand test reached {_amount(f, f['result'])}, which is {f['ratio']:.0%} of the target of "
        f"{_amount(f, f['target'])} set in advance."),
    "close_to_target": lambda f: (
        f"The demand test reached {_amount(f, f['result'])}, which is {f['ratio']:.0%} of the target of "
        f"{_amount(f, f['target'])}. That's close, so it's worth running the test once more."),
    "close_after_retry": lambda f: f"After a second try the test still only reached {f['ratio']:.0%} of the target.",
    "far_below_target_alternative": lambda f: (
        f"The demand test reached {_amount(f, f['result'])}, only {f['ratio']:.0%} of the target of "
        f"{_amount(f, f['target'])}. But the research points to a different customer group that may want it more."),
    "far_below_target": lambda f: (
        f"The demand test reached {_amount(f, f['result'])}, only {f['ratio']:.0%} of the target of "
        f"{_amount(f, f['target'])} set in advance: less than half. It's better to stop or change direction than to "
        "move the goalposts."),
    "result_unit_mismatch": lambda f: (
        f"The result entered ({f['result']:g}) can't be compared with the target set in advance, which is a share of "
        f"visitors ({_amount(f | {'rate': True}, f['target'])} for {f['metric']}). Comparing a count with a share would "
        "give a meaningless answer, so please enter how many people visited and how many signed up or bought, and "
        "the team will work out the share."),
    "no_visitors": lambda f: (
        "No visitors were entered, so there is no share to compare with the target yet. Enter how many people visited "
        "and how many signed up or bought."),
    # Gate 3
    "promise_unmeetable": lambda f: (
        "The product as planned can't deliver what the landing page promises with the formats the team can build."),
    "all_tests_pass": lambda f: (
        f"The product passes all {f['total']} quality checks (after {f['loops']} build round(s))."),
    "tests_failing": lambda f: (
        f"{f['total'] - f['passed']} of {f['total']} quality checks still fail after {f['loops']} build rounds. "
        "Trying once more with a smaller first version usually fixes this."),
    "tests_failing_after_repeats": lambda f: (
        f"Even after a smaller retry, only {f['passed']} of {f['total']} quality checks pass."),
    # Gate 4
    "wording_violations": lambda f: (
        f"{plural(len(f['violations']), 'wording problem')} still need fixing before launch: "
        + "; ".join(violation_text(v) for v in f["violations"][:3]) + "."),
    "price_outside_range": lambda f: (
        f"The price of ${f['price']:g} is outside what similar products charge (${f['range'][0]:g} to "
        f"${f['range'][1]:g}), and there's no clear reason why."),
    "clean_price_sourced": lambda f: (
        f"The texts are clean, and the price of ${f['price']:g} is in line with similar products "
        f"(${f['range'][0]:g} to ${f['range'][1]:g})."),
    "clean_price_assumed": lambda f: (
        "The texts are clean. The team couldn't find competitor prices, so the price is a reasoned guess (ASSUMPTION)."),
    # Gate 5
    "launch_withdrawn": lambda f: "You withdrew the launch.",
    "launch_not_ready": _launch_not_ready,
    "launch_ready": lambda f: (
        f"All {f['published']} item(s) you approved were recorded (publishing MOCKED: nothing went online), and the "
        "practice sale, delivery and refund all worked (payments MOCKED)."),
    # Gate 6
    "memo_fails_check": lambda f: (
        "The review of results didn't match the numbers, so it's being rewritten before you decide."),
    "targets_met": lambda f: (
        "The results meet the targets set in advance" + (" (practice data, MOCKED)" if f["mocked"] else "") + ": keep going."),
    "targets_missed_alternative": lambda f: (
        "The results miss the targets (" + "; ".join(metric_miss(m) for m in f["misses"])
        + "), but the research points to a promising alternative."),
    "targets_missed": lambda f: (
        "The results miss the targets (" + "; ".join(metric_miss(m) for m in f["misses"])
        + "), and no alternative is backed by evidence."),
}


def gate_reason(code: str, facts: dict) -> str:
    """The plain-words sentence for a gate's reason code."""
    return GATE_REASONS[code](facts)
