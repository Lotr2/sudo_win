"""Web UI API: a brief starts a run; chat never approves; buttons do."""

import time

from fastapi.testclient import TestClient

from fakes import FakeSearch, fake_fetch
from venture_builder.runtime import Runtime
from venture_builder.ui.server import create_app


def _wait(c, run_id, kind=None, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"/api/runs/{run_id}").json()
        if not st["busy"] and (kind is None or (st["pending"] and st["pending"]["kind"] == kind)):
            return st
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {kind}: {st['pending']}")


def test_ui_flow(settings, router):
    app = create_app(settings, runtime_factory=lambda brief: Runtime.new(settings, brief, router=router,
                                                                          search=FakeSearch(), fetch=fake_fetch))
    c = TestClient(app)
    assert "Venture Builder" in c.get("/").text
    assert c.get("/api/info").json()["labels"]["payments"] == "MOCKED"
    run_id = c.post("/api/runs", json={"brief": "I'm a designer selling invoice templates; $200, 8 h/week."}).json()["run_id"]
    _wait(c, run_id, "readback")
    c.post(f"/api/runs/{run_id}/chat", json={"text": "yes"})
    st = _wait(c, run_id, "gate")
    p = st["pending"]
    assert c.post(f"/api/runs/{run_id}/gate", json={"decision": "go", "code": "000000"}).status_code == 400
    c.post(f"/api/runs/{run_id}/gate", json={"decision": p["recommended"], "code": p["code"],
                                             "variant_id": p.get("suggested_variant")})
    st = _wait(c, run_id)
    while st["pending"]["kind"] != "approval":
        c.post(f"/api/runs/{run_id}/chat", json={"text": "fine"})
        st = _wait(c, run_id)
    item = st["pending"]["items"][0]
    c.post(f"/api/runs/{run_id}/chat", json={"text": "I approve"})
    st = _wait(c, run_id, "approval")
    assert st["outbox"][0]["status"] == "pending"
    # Asking for changes is tied to the version shown, like an approval.
    assert c.post(f"/api/runs/{run_id}/items/{item['id']}/reject", json={"note": "x"}).status_code == 422
    c.post(f"/api/runs/{run_id}/items/{item['id']}/reject", json={"note": "x", "hash_prefix": "000000"})
    assert next(i for i in _wait(c, run_id)["outbox"] if i["id"] == item["id"])["status"] == "pending"
    c.post(f"/api/runs/{run_id}/items/{item['id']}/approve", json={"hash_prefix": item["hash"]})
    st = _wait(c, run_id)
    assert next(i for i in st["outbox"] if i["id"] == item["id"])["status"] == "approved"


def test_continue_only_when_paused_and_plain_labels(settings, router):
    app = create_app(settings, runtime_factory=lambda brief: Runtime.new(settings, brief, router=router,
                                                                          search=FakeSearch(), fetch=fake_fetch))
    c = TestClient(app)
    assert c.get("/api/info").json()["text"]["phases"]["1"] == "Explore the idea"
    run_id = c.post("/api/runs", json={"brief": "I'm a designer selling invoice templates; $200, 8 h/week."}).json()["run_id"]
    st = _wait(c, run_id, "readback")
    assert c.post(f"/api/runs/{run_id}/continue", json={}).status_code == 409   # waiting for the founder, not paused
    assert all({"file", "label", "phase"} <= set(d) for d in st["documents"])
