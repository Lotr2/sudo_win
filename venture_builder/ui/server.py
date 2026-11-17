"""Web UI and API. The founder submits a brief, follows streaming progress, chats with the Founder Liaison,
reviews gate memos, and approves or rejects each gate and outbox item with buttons. Chat text never
approves anything: approvals and gate decisions have their own endpoints, which only the buttons call."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..approval import FounderConfirmation, gate_token
from ..config import Settings
from ..runtime import Runtime

STATIC = Path(__file__).parent / "static"


class Handle:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.lock = threading.Lock()      # serialises work on the run
        self._flag = threading.Lock()     # guards the busy flag only
        self.busy = False
        self.error: str | None = None
        self.question_since: float | None = None

    def run(self, fn: Callable, *args, **kwargs) -> None:
        """Run a blocking runtime call (model calls can take minutes) in the background."""
        with self._flag:
            if self.busy:
                raise HTTPException(409, "the run is busy; wait for the current step to finish")
            self.busy, self.error = True, None

        def work():
            with self.lock:
                try:
                    fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - shown in the UI, run stays resumable
                    self.error = f"{type(exc).__name__}: {exc}"
                    self.rt.ctx.store.progress(f"error: {self.error}", kind="error")
                finally:
                    self.busy = False
                    self.question_since = time.monotonic()

        threading.Thread(target=work, daemon=True).start()


class BriefIn(BaseModel):
    brief: str


class TextIn(BaseModel):
    text: str


class ApproveIn(BaseModel):
    hash_prefix: str


class RejectIn(BaseModel):
    note: str


class GateIn(BaseModel):
    decision: str
    code: str
    variant_id: str | None = None
    note: str = ""


class FormIn(BaseModel):
    values: dict


class ConfirmIn(BaseModel):
    field: str
    value: str | None = None


def create_app(settings: Settings, runtime_factory: Callable[..., Runtime] | None = None) -> FastAPI:
    app = FastAPI(title="Venture Builder")
    handles: dict[str, Handle] = {}

    def new_runtime(brief: str) -> Runtime:
        return runtime_factory(brief) if runtime_factory else Runtime.new(settings, brief)

    def handle(run_id: str) -> Handle:
        if run_id not in handles:
            if not (settings.runs_dir / run_id / "dossier.json").exists():
                raise HTTPException(404, "no such run")
            handles[run_id] = Handle(Runtime(settings, run_id))
        return handles[run_id]

    def demo_timer():
        """Demo mode: questions time out after 30 s and stay labelled assumptions (never approvals)."""
        while True:
            time.sleep(2)
            for h in list(handles.values()):
                p = None if h.busy else h.rt.pending()
                if p and p.get("timeout") and h.question_since and time.monotonic() - h.question_since > p["timeout"]:
                    h.question_since = None
                    h.run(h.rt.timeout)

    if settings.demo:
        threading.Thread(target=demo_timer, daemon=True).start()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info():
        return {"single_model": settings.single_model, "model_oss": settings.model_oss,
                "model_gemma": None if settings.single_model else settings.model_gemma, "mode": settings.run_mode,
                "labels": {"payments": "MOCKED", "publishing": "MOCKED", "emails": "MOCKED",
                           "event_log": "MOCKED", "legal_review": "STUBBED", "web_tool_browser_test": "STUBBED"}}

    @app.get("/api/runs")
    def runs():
        out = []
        if settings.runs_dir.exists():
            for d in sorted(settings.runs_dir.iterdir(), reverse=True):
                if (d / "dossier.json").exists():
                    info_ = json.loads((d / "dossier.json").read_text())
                    out.append({"run_id": d.name, "phase": info_["phase"], "status": info_["status"],
                                "brief": info_["brief"][:80]})
        return out

    @app.post("/api/runs")
    def create(body: BriefIn):
        if len(body.brief.strip()) < 20:
            raise HTTPException(400, "write a few sentences: idea, skills, budget, time, market, values")
        rt = new_runtime(body.brief)
        h = handles[rt.run_id] = Handle(rt)
        h.run(rt.advance)
        return {"run_id": rt.run_id}

    @app.get("/api/runs/{run_id}")
    def status(run_id: str):
        h = handle(run_id)
        st = h.rt.status()
        p = st["pending"]
        if p and p["kind"] == "approval":
            for it in p["items"]:
                it["status"] = h.rt.ctx.outbox.get(it["id"])["status"]
        return st | {"busy": h.busy, "error": h.error,
                     "conversation": h.rt.ctx.store.conversation()[-80:]}

    @app.get("/api/runs/{run_id}/outputs/{name}")
    def output(run_id: str, name: str):
        h = handle(run_id)
        path = (h.rt.ctx.store.outputs / name).resolve()
        if path.parent != h.rt.ctx.store.outputs.resolve() or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="text/plain; charset=utf-8" if not name.endswith(".html") else "text/html")

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str):
        h = handle(run_id)

        async def stream():
            sent = 0
            while True:
                rows = h.rt.ctx.store.progress_events(sent)
                for row in rows:
                    yield f"data: {json.dumps(row)}\n\n"
                sent += len(rows)
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/chat")
    def chat(run_id: str, body: TextIn):
        h = handle(run_id)
        h.run(h.rt.reply, body.text)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/form")
    def form(run_id: str, body: FormIn):
        h = handle(run_id)
        h.run(h.rt.submit_input, body.values)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/items/{item_id}/approve")
    def approve(run_id: str, item_id: str, body: ApproveIn):
        h = handle(run_id)
        conf = FounderConfirmation(channel="ui", item_ref=item_id, typed_hash_prefix=body.hash_prefix)
        h.run(h.rt.approve, item_id, conf)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/items/{item_id}/reject")
    def reject(run_id: str, item_id: str, body: RejectIn):
        h = handle(run_id)
        h.run(h.rt.reject, item_id, body.note or "(no note)", "ui")
        return {"ok": True}

    @app.post("/api/runs/{run_id}/gate")
    def gate(run_id: str, body: GateIn):
        h = handle(run_id)
        p = h.rt.pending()
        if not p or p["kind"] != "gate":
            raise HTTPException(409, "no gate decision is waiting")
        if body.code != gate_token(p["gate"], p["attempt"])[:6]:
            raise HTTPException(400, "wrong confirmation code")
        crit = [a.field for a in h.rt.ctx.assumptions.critical_unconfirmed(f"gate{p['gate']}")]
        if body.decision == "go" and crit:
            raise HTTPException(409, "GO rests on unconfirmed decision-critical assumptions: " + ", ".join(crit))
        conf = FounderConfirmation(channel="ui", item_ref=f"gate{p['gate']}-{p['attempt']}", typed_hash_prefix=body.code)
        h.run(h.rt.decide_gate, body.decision, conf, variant_id=body.variant_id, note=body.note)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/confirm")
    def confirm(run_id: str, body: ConfirmIn):
        h = handle(run_id)
        h.run(h.rt.confirm_assumption, body.field, body.value)
        return {"ok": True}

    return app
