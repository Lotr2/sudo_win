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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..approval import FounderConfirmation
from .. import labels
from . import readable
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
                raise HTTPException(409, "busy: your team is still working on the last step. Please give it a moment.")
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
    brand: dict | None = None
    data_mode: str = "real"


class BrandIn(BaseModel):
    name: str = ""
    color: str = ""
    logo: str = ""


class TextIn(BaseModel):
    text: str


class ApproveIn(BaseModel):
    hash_prefix: str


class RejectIn(BaseModel):
    note: str
    hash_prefix: str


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
    from ..approval import SIMULATION_ENV, simulation_enabled
    if simulation_enabled():
        raise RuntimeError(f"{SIMULATION_ENV}=1 is only for scripts/simulate_founder.py; the web UI never runs "
                           "with a simulated founder")
    app = FastAPI(title="Venture Builder")
    handles: dict[str, Handle] = {}

    def new_runtime(brief: str) -> Runtime:
        return runtime_factory(brief) if runtime_factory else Runtime.new(settings, brief)

    def handle(run_id: str) -> Handle:
        if run_id not in handles:
            if not (settings.runs_dir / run_id / "dossier.json").exists():
                raise HTTPException(404, "We couldn't find that project.")
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
                "text": labels.as_dict(),
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
            raise HTTPException(400, "Please describe your idea in a sentence or two, so the team has something to work with.")
        from ..landing import clean_brand
        try:
            brand = clean_brand(body.brand) if body.brand else {}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if body.data_mode not in {"real", "practice"}:
            raise HTTPException(400, "The project must be real or practice.")
        rt = new_runtime(body.brief)
        if body.data_mode != rt.dossier.data_mode:
            rt.set_data_mode(body.data_mode)
        if brand:
            rt.set_brand(brand)
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
        docs = [{"file": o, "label": labels.doc_label(o), "phase": int(o[5]) if o[:5] == "phase" and o[5:6].isdigit() else 0}
                for o in st["outputs"]]
        return st | {"busy": h.busy, "error": h.error, "documents": docs,
                     "conversation": h.rt.ctx.store.conversation()[-80:]}

    @app.get("/api/runs/{run_id}/outputs/{name}")
    def output(run_id: str, name: str, view: int = 0):
        h = handle(run_id)
        path = (h.rt.ctx.store.outputs / name).resolve()
        if path.parent != h.rt.ctx.store.outputs.resolve() or not path.exists():
            raise HTTPException(404, "We couldn't find that document.")
        if view and name.endswith((".md", ".json", ".txt", ".csv")):
            return HTMLResponse(readable.document_page(name, path.read_text()))
        return FileResponse(path, media_type="text/plain; charset=utf-8" if not name.endswith(".html") else "text/html")

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request):
        h = handle(run_id)

        async def stream():
            sent = 0
            while not await request.is_disconnected():
                rows = h.rt.ctx.store.progress_events(sent)
                for row in rows:
                    row["friendly"] = labels.event_text(row)
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
        conf = FounderConfirmation(channel="ui", item_ref=item_id, typed_hash_prefix=body.hash_prefix)
        h.run(h.rt.reject, item_id, body.note or "(no note)", conf)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/gate")
    def gate(run_id: str, body: GateIn):
        h = handle(run_id)
        p = h.rt.pending()
        if not p or p["kind"] != "gate":
            raise HTTPException(409, "There is no decision waiting right now.")
        if body.code.lower() != p["code"]:
            raise HTTPException(400, "That confirmation didn't match. Please reload the page and try again.")
        crit = [a.field for a in h.rt.ctx.assumptions.critical_unconfirmed(f"gate{p['gate']}")]
        if body.decision == "go" and crit:
            raise HTTPException(409, "Before going ahead, please confirm these guesses first: "
                                + ", ".join(c.replace("_", " ") for c in crit))
        conf = FounderConfirmation(channel="ui", item_ref=f"gate{p['gate']}-{p['attempt']}", typed_hash_prefix=body.code)
        h.run(h.rt.decide_gate, body.decision, conf, variant_id=body.variant_id, note=body.note)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/brand")
    def brand(run_id: str, body: BrandIn):
        """Set or change the brand; it is used the next time the landing page is written."""
        h = handle(run_id)
        try:
            return {"brand": {k: (v if k != "logo" else True) for k, v in h.rt.set_brand(body.model_dump()).items()}}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/runs/{run_id}/skip")
    def skip(run_id: str):
        h = handle(run_id)
        p = h.rt.pending()
        if not p or p["kind"] not in {"question", "readback", "notes"}:
            raise HTTPException(409, "There's no question to skip right now.")
        h.run(h.rt.skip)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/continue")
    def continue_run(run_id: str):
        """Resume a run whose step was cut off (e.g. the app restarted mid-step). Approvals are never implied."""
        h = handle(run_id)
        if not h.rt.interrupted() and not h.error:
            raise HTTPException(409, "This project isn't paused: there is nothing to continue.")
        h.run(h.rt.continue_interrupted)
        return {"ok": True}

    @app.post("/api/runs/{run_id}/confirm")
    def confirm(run_id: str, body: ConfirmIn):
        h = handle(run_id)
        h.run(h.rt.confirm_assumption, body.field, body.value)
        return {"ok": True}

    return app
