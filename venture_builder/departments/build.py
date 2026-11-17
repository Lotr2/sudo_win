"""Phase 3 · Build: plan-and-execute, then a build–test loop.

Product Manager (spec as acceptance criteria) → Liaison reads the spec back → Template Picker [code] →
Builder ⇄ QA Runner [code, sandbox, max 3 loops] → Docs Writer → Gate 3 → founder approves the MVP.
"""

from __future__ import annotations

import json

from .. import gates, qa, templates
from ..agents import builder, docs_writer, founder_liaison, gate_reporter, product_manager
from ..context import Ask, Context, Next
from .common import decision, gate_markdown, present_gate, stop_run


def _variant(ctx: Context):
    return next(v for v in ctx.dossier.variants if v.id == ctx.dossier.chosen_variant)


def p3_start(ctx: Context, answer) -> Next:
    ctx.dossier.phase = 3
    ctx.note("Phase 3 · Build started")
    return Next("p3_spec")


def p3_spec(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    smaller = d.count("gate3_repeat") > 0
    v = _variant(ctx)
    payload = {"variant": v.model_dump(), "canvas_solution": d.canvas.solution.text,
               "landing_promise": d.canvas.unique_value.text, "product_form": v.product_form,
               "founder_hours_per_week": d.profile.hours_per_week,
               "make_it_smaller": smaller, "founder_notes": [x["text"] for x in d.founder_inputs if x.get("phase") == 3]}
    spec = product_manager.run(ctx, payload)
    spec.product_form = templates.pick(v.product_form if v.product_form in templates.FORMS else spec.product_form)
    if spec.product_form == "template_pack" and spec.item_count <= 0:
        spec.item_count = 5
    if smaller:
        spec.min_words_per_section = min(spec.min_words_per_section, 60)
    d.spec = spec
    _write_spec(ctx)
    msg = founder_liaison.message(ctx, "read_back_spec", spec=spec.model_dump())
    ctx.say(msg + " Anything missing?", kind="question")
    return Ask("question", {"question": msg + " Anything missing?", "why": "spec read-back", "optional": True},
               resume_to="p3_spec_answer", timeout=ctx.question_timeout())


def p3_spec_answer(ctx: Context, answer) -> Next:
    d = ctx.dossier
    text = answer if isinstance(answer, str) else (answer or {}).get("text") if answer else None
    if text and text.strip().lower() not in {"no", "nope", "looks good", "ok", "fine", "no, looks good", "none"} \
            and d.count("spec_revisions") < 1:
        d.bump("spec_revisions")
        d.founder_inputs.append({"phase": 3, "type": "founder_reported", "text": text})
        return Next("p3_spec")
    return Next("p3_build")


def p3_build(ctx: Context, answer) -> Next:
    d = ctx.dossier
    spec = d.spec.model_dump()
    form = d.spec.product_form
    out = ctx.store.dir / "product"
    templates.scaffold(form, spec, out)
    failures: list[str] = []
    previous = None
    report = {"total": 0, "passed": 0, "failures": []}
    loops = ctx.t["limits"]["build_loops"]
    for loop in range(1, loops + 1):
        content = builder.run(ctx, form, spec, failures, previous)
        previous = content.model_dump()
        templates.render(form, previous, out)
        report = qa.run_acceptance(out)
        ctx.note(f"Build loop {loop}: {report['passed']}/{report['total']} acceptance tests pass "
                 f"({report['isolation']})")
        if report["total"] and report["passed"] == report["total"]:
            break
        failures = report["failures"] or [report["output"][-800:]]
        ctx.say(f"Build loop {loop}: {report['total'] - report['passed']} test(s) still fail; the Builder is "
                "fixing them.", kind="progress")
    d.build = {"dir": str(out), "form": form, "loops": loop, "report": {k: v for k, v in report.items() if k != "output"},
               "content": previous}
    _write_test_report(ctx, report, loop)
    return Next("p3_docs")


def p3_docs(ctx: Context, answer) -> Next:
    d = ctx.dossier
    summary = json.dumps(d.build.get("content") or {})[:4000]
    docs = docs_writer.run(ctx, d.spec.model_dump(), summary)
    d.docs = docs
    md = [f"# Phase 3 · Step 3: User guide: {d.spec.title}", "", docs.user_guide, "", "## FAQ", ""]
    for f in docs.faq:
        md += [f"**{f.q}**", "", f.a, ""]
    ctx.store.write_output("phase3_step3_user_guide.md", "\n".join(md))
    return Next("p3_gate")


def p3_gate(ctx: Context, answer) -> Ask:
    d = ctx.dossier
    r = d.build["report"]
    res = gates.gate3(tests_passed=r["passed"], tests_total=r["total"], loops_used=d.build["loops"],
                      repeated=d.count("gate3_repeat") > 0, promise_unmeetable=False, t=ctx.t)
    memo = gate_reporter.run(ctx, 3, res.model_dump(), {"spec": d.spec.model_dump(),
                                                        "failures": r.get("failures", [])}).model_dump()
    md = gate_markdown(ctx, res, memo, "Does the product exist and work?", "gate3")
    md += f"\nPreview the product in `runs/{d.run_id}/product/` before approving the MVP.\n"
    return present_gate(ctx, res, md, "p3_gate_decided", {"preview_dir": d.build["dir"]})


def p3_gate_decided(ctx: Context, answer) -> Next:
    d = ctx.dossier
    rec = decision(ctx, 3)
    if rec["decision"] == "stop":
        return Next(stop_run(ctx, 3, "; ".join(d.gates["3"].reasons)))
    if rec["decision"] == "repeat":
        d.bump("gate3_repeat")
        return Next("p3_spec")
    if rec["decision"] == "pivot":
        d.lessons.append("Gate 3 pivot: " + "; ".join(d.gates["3"].reasons))
        d.phase = 1
        return Next("p1_scout")
    return Next("p4_start")


def _write_spec(ctx: Context) -> None:
    s = ctx.dossier.spec
    md = [f"# Phase 3 · Step 1: Spec: {s.title}", "", s.summary, "", f"**Template:** {s.product_form}", "",
          "## Features", ""] + [f"- {f}" for f in s.features] + ["", "## Acceptance criteria (machine-checked)", ""]
    if s.product_form == "guide":
        md += [f"- Section **{x.heading}** exists ({x.purpose}) with at least {s.min_words_per_section} words"
               for x in s.sections]
    elif s.product_form == "template_pack":
        md += [f"- Exactly {s.item_count} templates", f"- Every template has: {', '.join(s.item_fields)}",
               "- An index lists every template"]
    else:
        md += [f"- Number input `{i.id}` ({i.label})" for i in s.inputs]
        md += ["- A Calculate button and a result area", "- No external resources, network calls or eval"]
    md += ["- No income promises, fake urgency or testimonials"]
    ctx.store.write_output("phase3_step1_spec.md", "\n".join(md) + "\n")


def _write_test_report(ctx: Context, report: dict, loops: int) -> None:
    md = ["# Phase 3 · Step 2: Test report", "",
          f"- Result: **{report['passed']}/{report['total']} acceptance tests pass** after {loops} build loop(s)",
          f"- Sandbox: {report.get('isolation', '')}", ""]
    if report.get("failures"):
        md += ["## Remaining failures", ""] + [f"- {f}" for f in report["failures"]]
    md += ["", "## Runner output (tail)", "", "```", report.get("output", "")[-2500:], "```"]
    if ctx.dossier.spec.product_form == "web_tool":
        md += ["", "Browser behaviour of the web tool is not executed by the tests (STUBBED); the founder tries "
               "the preview before approving."]
    ctx.store.write_output("phase3_step2_test_report.md", "\n".join(md) + "\n")


NODES = {"p3_start": p3_start, "p3_spec": p3_spec, "p3_spec_answer": p3_spec_answer, "p3_build": p3_build,
         "p3_docs": p3_docs, "p3_gate": p3_gate, "p3_gate_decided": p3_gate_decided}
