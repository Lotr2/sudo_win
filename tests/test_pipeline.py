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
    log = json.loads(rt.ctx.store.path("decision_log.json").read_text())
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
    log = rt.ctx.store.path("decision_log.json").read_text()
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
