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


def test_status_never_reports_idle_with_a_stale_pending_value(settings, router, monkeypatch):
    """Regression: `pending` and `busy` were read at different moments, so a step finishing in between gave
    'idle, nothing (or the old thing) pending' (seen as an intermittent failure of test_ui_flow)."""
    import threading
    app = create_app(settings, runtime_factory=lambda brief: Runtime.new(settings, brief, router=router,
                                                                          search=FakeSearch(), fetch=fake_fetch))
    c = TestClient(app)
    run_id = c.post("/api/runs", json={"brief": "I'm a designer selling invoice templates; $200, 8 h/week."}).json()["run_id"]
    _wait(c, run_id, "readback")
    step_may_finish = threading.Event()
    real_reply, real_status = Runtime.reply, Runtime.status

    def slow_reply(self, text):              # a step that runs until the status call has read `pending`
        step_may_finish.wait(5)
        return real_reply(self, text)

    def status_racing_the_step(self):
        st = real_status(self)                # pending read while the step is still running
        step_may_finish.set()
        time.sleep(0.5)                       # the step finishes before the endpoint reads `busy`
        return st

    monkeypatch.setattr(Runtime, "reply", slow_reply)
    c.post(f"/api/runs/{run_id}/chat", json={"text": "yes"})
    monkeypatch.setattr(Runtime, "status", status_racing_the_step)
    st = c.get(f"/api/runs/{run_id}").json()
    monkeypatch.setattr(Runtime, "status", real_status)
    assert st["busy"] is True                 # the stale pending value is never presented as the idle state
    assert _wait(c, run_id)["pending"]["kind"] == "gate"
