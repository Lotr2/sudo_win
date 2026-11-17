"""Each phase's pipeline runs end to end on the fake client and writes its outputs with the correct names."""

import json
import re

from conftest import drive

EXPECTED = [
    "phase1_step1_opportunities.md", "phase1_step2_scores.json", "phase1_step3_interview_script.md",
    "phase1_gate_decision.md", "phase2_step1_lean_canvas.md", "phase2_step2_experiment.md",
    "phase2_step3_landing_page.html", "phase2_gate_decision.md", "phase3_step1_spec.md",
    "phase3_step2_test_report.md", "phase3_step3_user_guide.md", "phase3_gate_decision.md",
    "phase4_step1_pricing.md", "phase4_step2_policies.md", "phase4_gate_decision.md",
    "phase5_step1_store_page.md", "phase5_step2_launch_posts.md", "payment_test_log.md",
    "phase5_gate_decision.md", "phase6_step1_metrics.md", "phase6_step2_support_log.md",
    "phase6_step3_pivot_memo.md", "phase6_gate_decision.md",
]
NAME = re.compile(r"^(phase\d+_(step\d+|gate)_[\w-]+\.(md|json|csv|txt|html)|payment_test_log\.md)$")


def test_full_pipeline_all_six_phases(make_runtime):
    rt = make_runtime()
    drive(rt)
    files = sorted(p.name for p in rt.ctx.store.outputs.iterdir())
    for name in EXPECTED:
        assert name in files, name
    assert all(NAME.match(f) for f in files), files
    assert rt.dossier.status == "finished"
    assert "PASS" in (rt.ctx.store.outputs / "payment_test_log.md").read_text()
    for n in "123456":
        assert n in rt.dossier.gates


def test_every_log_entry_has_required_keys(make_runtime):
    rt = make_runtime()
    drive(rt)
    log = rt.ctx.store.log.entries
    assert len(log) > 50
    for e in log:
        for k in ("timestamp", "agent", "action", "sources", "model", "cost", "approved_by"):
            assert k in e, (k, e)
        assert isinstance(e["sources"], list) and isinstance(e["cost"], (int, float))
    assert any(e["approved_by"] == "founder" for e in log)


def test_landing_page_is_designed_safe_and_self_contained():
    import re
    from venture_builder import landing
    from venture_builder.schemas import LandingCopy
    from fakes import FakeModels
    copy = LandingCopy.model_validate(FakeModels().landing_copywriter({}))
    copy.headline = 'Get paid <script>alert(1)</script> on time'
    page = landing.render(copy, product_name="Get Paid Kit", product_form="template_pack")
    assert "<script>" not in page and "&lt;script&gt;" in page           # model text is always escaped
    assert not re.search(r'(src|href)=["\']https?://', page)            # nothing loaded from outside
    assert "How it works" in page and "What you get" in page and "<details>" in page
    visible = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    assert not re.search(r"\btestimonials?\b|\breviews?\b|\bratings?\b|\bjoin (over|thousands)|\bcountdown", visible, re.I)
    assert "form is not connected yet" in page                           # honest about the MOCKED form


def test_landing_layouts_brand_and_validation():
    import base64
    import pytest
    from venture_builder import landing
    from venture_builder.schemas import LandingCopy
    from fakes import FakeModels
    base = FakeModels().landing_copywriter({})
    pages = {}
    for layout in ("product", "story", "minimal"):
        copy = LandingCopy.model_validate({**base, "layout": layout, "font_style": "classic"})
        pages[layout] = landing.render(copy, product_name="Kit", product_form="guide",
                                       brand={"name": "Studio Nour", "color": "#f2c94c"})
        assert f'class="{layout}-page"' in pages[layout] and "Studio Nour" in pages[layout]
        assert "#f2c94c" in pages[layout] and "Iowan Old Style" in pages[layout]
    assert pages["story"].count('class="quotes"') == 1                     # story opens with the frustrations
    assert landing.palette("#f2c94c")["on"] == "#111318"                    # light brand colour: dark text on it
    assert landing.palette("#1d3f63")["on"] == "#ffffff"
    order = landing.section_order(LandingCopy.model_validate({**base, "section_order": ["faq", "faq", "steps"]}), "product")
    assert order[:2] == ["faq", "steps"] and {"inside", "features"} <= set(order)   # essentials always kept, no repeats
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
    assert landing.clean_brand({"logo": png})["logo"] == png
    for bad in ({"color": "red"}, {"color": "#12345g"}, {"logo": "data:image/svg+xml;base64,PHN2Zz4="},
                {"logo": "data:image/png;base64," + base64.b64encode(b"not a png").decode()},
                {"logo": "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 120_000).decode()},
                {"name": "x" * 61}):
        with pytest.raises(ValueError):
            landing.clean_brand(bad)


def test_brand_flows_from_the_api_to_the_page(settings, router):
    import time
    from fastapi.testclient import TestClient
    from fakes import FakeSearch, fake_fetch
    from venture_builder.runtime import Runtime
    from venture_builder.ui.server import create_app
    app = create_app(settings, runtime_factory=lambda b: Runtime.new(settings, b, router=router, search=FakeSearch(),
                                                                        fetch=fake_fetch))
    c = TestClient(app)
    assert c.post("/api/runs", json={"brief": "I'm a designer selling invoice templates; $200, 8 h/week.",
                                     "brand": {"color": "nope"}}).status_code == 400
    rid = c.post("/api/runs", json={"brief": "I'm a designer selling invoice templates; $200, 8 h/week.",
                                    "brand": {"name": "Studio Nour", "color": "#7a3cff"}}).json()["run_id"]
    rt = Runtime(settings, rid)
    assert rt.dossier.brand == {"name": "Studio Nour", "color": "#7a3cff"}
    log = rt.ctx.store.log.path.read_text()
    assert '"set_brand"' in log


def test_landing_copy_never_shows_internal_labels():
    from venture_builder import landing
    from venture_builder.schemas import LandingCopy
    from fakes import FakeModels
    raw = FakeModels().landing_copywriter({})
    raw["headline"] = "Get paid on time (ASSUMPTION)"
    raw["closing"] = "Join the waitlist [MOCKED] now"
    page = landing.render(LandingCopy.model_validate(raw), product_name="Kit")
    import re
    visible = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    assert "ASSUMPTION" not in visible and "MOCKED" not in visible and "Get paid on time" in visible


def test_builder_revises_once_after_a_weak_quality_review(make_runtime, models):
    from conftest import drive
    models.product_review = lambda p: {"score": 2, "issues": ["Template 1 has no example email"]}
    rt = make_runtime()
    drive(rt)
    review = rt.dossier.build["review"]
    assert {k: review[k] for k in ("score", "issues", "revised")} == \
        {"score": 2, "issues": ["Template 1 has no example email"], "revised": True}
    assert review["final_score"] == 2                                             # the revision is re-scored
    assert models.reviews == 2 and models.calls.count("builder") >= 2            # review, revision, re-review
    report = (rt.ctx.store.outputs / "phase3_step2_test_report.md").read_text()
    assert "2 out of 5" in report and "Template 1 has no example email" in report
    assert rt.dossier.build["report"]["passed"] == rt.dossier.build["report"]["total"]


def test_research_loop_stops_when_the_bar_is_met_and_remembers_queries(make_runtime, models):
    from conftest import drive
    rt = make_runtime()
    drive(rt, stop_at_gate=1)
    log = rt.dossier.research_log
    assert log and all(entry["tried"] for entry in log.values())
    rounds = [e for e in rt.ctx.store.log.entries
              if e["action"] == "research_round"]
    assert rounds and all(e["readings"] >= 1 for e in rounds)
    # The fake evidence meets the bar in the first round, so the market research needs no second round.
    market = [e for e in rounds if e["agent"] == "market_researcher"]
    assert all(e["round"] == 1 for e in market) or any(not e["gaps"] for e in market)


def test_agreement_keeps_only_findings_most_readings_share():
    from venture_builder.departments.discover import agree
    from venture_builder.schemas import Finding
    F = lambda k, ids: Finding(kind=k, claim="c", evidence_ids=ids, source_type="first_hand", about_market=True)  # noqa: E731
    runs = [[F("pain", ["ev_1"]), F("pain", ["ev_2"]), F("channel", ["ev_3"])],
            [F("pain", ["ev_1", "ev_4"]), F("demand", ["ev_2"])],
            [F("pain", ["ev_1"]), F("channel", ["ev_3"])]]
    kept = agree(runs, 2)
    assert [(f.kind, f.evidence_ids) for f in kept] == [("pain", ["ev_1"]), ("channel", ["ev_3"])]


def test_spec_must_fill_its_own_form_and_only_that():
    import pytest
    from venture_builder.agents.product_manager import check, normalise
    from venture_builder.schemas import Spec
    mixed = Spec(title="Kit", summary="s", product_form="template_pack", features=["f"],
                 sections=[{"heading": "Why", "purpose": "p"}] * 4, item_count=5, item_fields=[])
    with pytest.raises(ValueError):
        check(mixed)                                   # a template pack without template fields is rejected
    good = mixed.model_copy(update={"item_fields": ["When to use", "Template", "Example"]})
    check(good)
    assert normalise(good).sections == [] and normalise(good).item_fields == ["When to use", "Template", "Example"]


def test_a_real_project_is_logged_and_labelled_as_real(make_runtime):
    import json
    from conftest import drive
    rt = make_runtime()                                    # projects are real unless the founder says practice
    real_numbers = {"result": 120, "mocked": False, "visitors": 1000, "orders": 40, "refunds": 1,
                    "tickets": "How do I get access?\nI want a refund please"}
    drive(rt, mocked=real_numbers)
    d = rt.dossier
    assert d.data_mode == "real" and d.signups["mocked"] is False and d.metrics["label"] == "founder-reported"
    assert d.gates["6"].mocked_evidence is False and d.gates["2"].mocked_evidence is False
    log = rt.ctx.store.log.entries
    assert log[0]["action"] == "start_project" and log[0]["mode"] == "real"
    assert all(e.get("project") == "real" for e in log)
    founder = [e for e in log if e["agent"] == "founder" or e["approved_by"] == "founder"]
    assert founder and all(e.get("real_founder") is True and "simulated_founder" not in e for e in founder)
    metrics_doc = (rt.ctx.store.outputs / "phase6_step1_metrics.md").read_text()
    assert "your real numbers" in metrics_doc and "practice data" not in metrics_doc.split("\n")[0]


def test_real_project_forms_default_to_real_and_practice_to_mocked(make_runtime):
    from conftest import confirm_gate, confirm_item
    for mode, expected_default in (("real", False), ("practice", True)):
        rt = make_runtime()
        if mode == "practice":
            rt.set_data_mode("practice")
        p = rt.advance()
        while p and p["kind"] != "mocked_input":
            if p["kind"] == "readback":
                rt.reply("yes")
            elif p["kind"] in {"question", "notes"}:
                rt.skip()
            elif p["kind"] == "approval":
                for it in p["items"]:
                    if rt.ctx.outbox.get(it["id"])["status"] == "pending":
                        rt.approve(it["id"], confirm_item(rt, it["id"]))
            elif p["kind"] == "gate":
                rt.decide_gate(p["recommended"] if p["recommended"] != "stop" else "go", confirm_gate(p),
                               variant_id=p.get("suggested_variant"))
            p = rt.pending()
        field = next(f for f in p["fields"] if f["name"] == "mocked")
        assert field["default"] is expected_default, mode


def test_api_records_the_project_mode(settings, router):
    from fastapi.testclient import TestClient
    from fakes import FakeSearch, fake_fetch
    from venture_builder.runtime import Runtime
    from venture_builder.ui.server import create_app
    c = TestClient(create_app(settings, runtime_factory=lambda b: Runtime.new(settings, b, router=router,
                                                                                 search=FakeSearch(), fetch=fake_fetch)))
    brief = "I'm a designer selling invoice templates; $200, 8 h/week."
    assert c.post("/api/runs", json={"brief": brief, "data_mode": "pretend"}).status_code == 400
    rid = c.post("/api/runs", json={"brief": brief, "data_mode": "practice"}).json()["run_id"]
    assert Runtime(settings, rid).dossier.data_mode == "practice"


# ---- decision log: JSON Lines per run, exported as a JSON array ----

def test_run_log_is_appended_line_by_line(tmp_path):
    import json
    from venture_builder.decision_log import REQUIRED_KEYS, DecisionLog, read_log
    path = tmp_path / "decision_log.jsonl"
    log = DecisionLog(path)
    log.append("scorer", "score_variants")
    first_line = path.read_text().splitlines()[0]
    log.append("gate", "gate1_discover", outcome="stop")
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and lines[0] == first_line           # earlier entries are never rewritten
    assert all(k in json.loads(lines[1]) for k in REQUIRED_KEYS)
    with path.open("a") as f:
        f.write('{"cut off by a cra')                            # a crash mid-write loses only that line
    assert [e["id"] for e in read_log(path)] == ["log_1", "log_2"]
    assert [e["id"] for e in DecisionLog(path).entries] == ["log_1", "log_2"]


def test_older_run_logs_are_migrated(tmp_path):
    import json
    from venture_builder.decision_log import DecisionLog
    old = [{"id": "log_1", "timestamp": "2026-11-17T09:00:00Z", "agent": "a", "action": "x", "sources": [],
            "model": None, "cost": 0, "approved_by": None}]
    (tmp_path / "decision_log.json").write_text(json.dumps(old))
    log = DecisionLog(tmp_path / "decision_log.jsonl")
    assert log.entries == old
    log.append("b", "y")
    assert [e["id"] for e in log.entries] == ["log_1", "log_2"]
    assert len((tmp_path / "decision_log.jsonl").read_text().splitlines()) == 2


# ---- an agent that keeps returning unusable output never crashes the run ----

def test_repeated_invalid_agent_output_pauses_the_run_instead_of_crashing(make_runtime, models):
    """Regression: the Product Manager returned an invalid spec twice in a row (router: one retry, then
    ModelOutputError), and the whole run crashed at the start of Stage 3."""
    from conftest import drive
    good = models.product_manager
    models.product_manager = lambda p: {"title": "x"}            # never a valid spec
    rt = make_runtime()
    p = drive(rt, stop_at_gate=3)
    assert p["kind"] == "gate" and p["gate"] == 3
    assert p["recommended"] == "repeat" and p["allowed"] == ["repeat", "stop"]
    assert "Writing the product plan" in p["reasons"][0] or "p3_spec" not in p["reasons"][0]
    assert any(e["action"] == "step_failed" and e["node"] == "p3_spec" for e in rt.ctx.store.log.entries)
    import pytest
    from conftest import confirm_gate
    with pytest.raises(ValueError):                                # go isn't offered: the step produced nothing
        rt.decide_gate("go", confirm_gate(p))
    models.product_manager = good                                  # the model recovers: repeat re-runs the step
    rt.decide_gate("repeat", confirm_gate(p))
    p = drive(rt, stop_at_gate=3)
    assert p["gate"] == 3 and p.get("allowed") is None and rt.dossier.spec is not None


def test_a_step_that_keeps_failing_recommends_stop(make_runtime, models):
    from conftest import confirm_gate, drive
    models.product_manager = lambda p: {"title": "x"}
    rt = make_runtime()
    p = drive(rt, stop_at_gate=3)
    rt.decide_gate("repeat", confirm_gate(p))
    p = rt.pending()
    assert p["kind"] == "gate" and p["recommended"] == "stop"
    rt.decide_gate("stop", confirm_gate(p))
    assert rt.dossier.status == "stopped"


# ---- the builder delivers the number of templates the spec asks for ----

def test_template_pack_gets_every_template_even_when_the_model_returns_one_at_a_time(make_runtime, models,
                                                                                   monkeypatch):
    """Regression (real run 20261117-191413-5967): the spec asked for 4 templates, the model returned 1 in every
    build round, and Gate 3 failed on "wrong number of templates: 1 != 4"."""
    from conftest import drive
    real_builder = models.builder

    def lazy_builder(p):
        out = real_builder(p)
        if p["product_form"] == "template_pack":
            out["items"] = out["items"][:1]
        return out

    models.builder = lazy_builder
    real_pm = models.product_manager

    def pack_pm(p):                                            # a valid template-pack spec (3-5 fields per template)
        out = real_pm(p)
        if p["product_form"] == "template_pack":
            out["item_fields"] = ["When to use", "Template", "Tips"]
        return out

    models.product_manager = pack_pm
    from venture_builder.departments import build
    monkeypatch.setattr(build, "_variant",                       # build the template-pack version of the idea
                        lambda ctx: next(v for v in ctx.dossier.variants if v.product_form == "template_pack"))
    rt = make_runtime()
    p = drive(rt, stop_at_gate=3)
    assert rt.dossier.spec.product_form == "template_pack"
    report = rt.dossier.build["report"]
    assert p["recommended"] == "go", (p["reasons"], report.get("failures"))
    assert len(list((rt.ctx.store.dir / "product" / "templates").glob("*.md"))) == rt.dossier.spec.item_count
    exported = (rt.ctx.store.outputs / "phase3_step2_product.md").read_text()
    assert exported.count("<!-- templates/") == rt.dossier.spec.item_count     # every template is in outputs/


# ---- the built product itself is part of the outputs ----

def test_built_product_is_written_to_outputs(make_runtime):
    """Gap: the MVP lived only under runs/<id>/product/ (git-ignored), so outputs/ held its spec, test report
    and user guide but never the product."""
    from conftest import drive
    rt = make_runtime()
    drive(rt, stop_at_gate=3)
    out = rt.ctx.store.outputs / "phase3_step2_product.md"
    assert out.exists(), sorted(p.name for p in rt.ctx.store.outputs.iterdir())
    text = out.read_text()
    product = (rt.ctx.store.dir / "product" / "guide.md").read_text()
    assert product.strip().splitlines()[0] in text           # the real built file, not a summary of it
    assert "MOCKED" not in text.splitlines()[0]


# ---- Stage 4 looks up what comparable products cost ----

class PricedSearch:
    """Like FakeSearch, but price searches find competitor pages that state prices, plus unrelated prices."""
    def __init__(self):
        from fakes import FakeSearch
        self.base, self.queries = FakeSearch(), []

    def __call__(self, query, limit=5):
        self.queries.append(query)
        if "price" in query.lower():
            return [{"url": "https://shop-a.example.com/kit", "title": "a",
                     "content": "Client Onboarding Template Pack for designers: $19 one-time download."},
                    {"url": "https://shop-b.example.net/bundle", "title": "b",
                     "content": "The freelance proposal guide and templates bundle costs $29."},
                    {"url": "https://news.example.org/x", "title": "c",
                     "content": "A mobile app design project cost $1500 and took 20 days."}]
        return self.base(query, limit)


def test_stage4_prices_are_sourced_from_comparable_products(make_runtime):
    """Gap: Stage 4 never searched for prices of the product it built, so Gate 4 said "couldn't find competitor
    prices" and the price stayed a guess, although ARCHITECTURE promises a price anchored on sourced prices."""
    from conftest import drive
    search = PricedSearch()
    rt = make_runtime(search=search)
    p = drive(rt, stop_at_gate=4)
    assert any("price" in q.lower() for q in search.queries)
    assert rt.dossier.build.get("price_range") == [19.0, 29.0]          # the $1500 app project is not a comparable
    assert p["recommended"] == "go" and "couldn't find competitor prices" not in p["reasons"][0]
    pricing = (rt.ctx.store.outputs / "phase4_step1_pricing.md").read_text()
    assert "$19 to $29" in pricing


def test_stage4_without_search_keeps_the_price_an_assumption(make_runtime):
    from fakes import FakeSearch
    from conftest import drive
    rt = make_runtime(search=FakeSearch())
    p = drive(rt, stop_at_gate=4)
    assert rt.dossier.build.get("price_range") is None
    assert "ASSUMPTION" in p["reasons"][0]
