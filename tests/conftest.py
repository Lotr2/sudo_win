"""Shared fixtures. Tests run without any model or key: a fake client is injected through the router."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeModels, FakeSearch, fake_fetch  # noqa: E402
from venture_builder.approval import FounderConfirmation  # noqa: E402
from venture_builder.config import load_settings  # noqa: E402
from venture_builder.llm_clients import FakeClient  # noqa: E402
from venture_builder.router import Router  # noqa: E402
from venture_builder.runtime import Runtime  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return load_settings({"RUNS_DIR": str(tmp_path / "runs"), "GEMINI_API_KEY": "test-key-not-real",
                          "OLLAMA_HOST": "http://127.0.0.1:9"}, dotenv=None)


@pytest.fixture
def models():
    return FakeModels()


@pytest.fixture
def router(settings, models):
    fake = FakeClient(models)
    return Router(settings, oss=fake, gemma=fake)


@pytest.fixture
def make_runtime(settings, router):
    def make(brief="I'm a designer. I want to sell invoice templates to freelance designers. Budget $200, 8 hours a week.",
             search=None, **kw):
        return Runtime.new(settings, brief, router=router, search=search or FakeSearch(), fetch=fake_fetch, **kw)
    return make


def confirm_item(rt, item_id):
    """TEST-ONLY founder fixture: what the CLI creates after an interactive TTY confirmation."""
    h = rt.ctx.outbox.get(item_id)["content_hash"].removeprefix("sha256:")[:8]
    return FounderConfirmation(channel="cli", item_ref=item_id, typed_hash_prefix=h)


def confirm_gate(p):
    """TEST-ONLY founder fixture for a gate decision."""
    from venture_builder.approval import gate_token
    return FounderConfirmation(channel="cli", item_ref=f"gate{p['gate']}-{p['attempt']}",
                               typed_hash_prefix=gate_token(p["gate"], p["attempt"])[:8])


def drive(rt, *, gate_choice=None, mocked=None, max_steps=200, stop_at_gate=None):
    """Answer every pause like a founder would, using the test-only fixtures."""
    mocked = mocked or {"result": 120, "mocked": True, "visitors": 1000, "orders": 40, "refunds": 1,
                        "tickets": "How do I get access?\nI want a refund please"}
    p = rt.advance() if not rt.started() else rt.pending()
    for _ in range(max_steps):
        if p is None:
            return None
        k = p["kind"]
        if k == "question":
            rt.reply("8 hours a week" if p.get("field") == "hours_per_week" else "Sounds fine to me")
        elif k == "readback":
            rt.reply("yes")
        elif k == "notes":
            rt.reply("Talked to 3 designers; two chase invoices every month.")
        elif k == "approval":
            for it in p["items"]:
                if rt.ctx.outbox.get(it["id"])["status"] == "pending":
                    rt.approve(it["id"], confirm_item(rt, it["id"]))
        elif k == "gate":
            if stop_at_gate == p["gate"]:
                return p
            choice = (gate_choice or {}).get(p["gate"], p["recommended"])
            rt.decide_gate(choice, confirm_gate(p), variant_id=p.get("suggested_variant") if p["gate"] == 1 else None)
        elif k == "mocked_input":
            rt.submit_input({f["name"]: mocked.get(f["name"], f.get("default", 0)) for f in p["fields"]})
        p = rt.pending()
    raise AssertionError("run did not finish")
