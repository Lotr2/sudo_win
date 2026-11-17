"""Runtime: drives one run's graph for the CLI and the web UI.

Founder actions arrive here as typed calls. Free text goes through `reply()`. It is checked by the Policy
Guard, it never approves anything, and it is passed to the graph only when the pending step is a
question. Approvals and gate decisions arrive through `approve()`, `reject()` and `decide_gate()`, which
need a `FounderConfirmation` that only the CLI (interactive TTY) and the UI (button) create.
"""

from __future__ import annotations

from urllib.parse import urlparse

import re
import shutil
from pathlib import Path

from langgraph.types import Command

from .agents import founder_liaison
from .approval import FounderConfirmation
from .config import ROOT, Settings
from .context import Context
from .decision_log import log_action
from .dossier import Dossier, RunStore, new_run_id
from .orchestrator import CONTEXTS, FIRST_NODE, NO_ANSWER, build_graph, sqlite_checkpointer
from .router import Router
from .tools.fetch import PageFetcher
from .tools.search import SearxSearch

QUESTION_KINDS = {"question", "readback", "notes"}
CONFIRM = re.compile(r"^\s*(yes|yep|yeah|correct|confirm(ed)?|that'?s (right|correct)|right|looks (good|right)|ok(ay)?)\b[.! ]*$", re.I)
CHAT_APPROVAL = re.compile(r"\b(i (approve|agree|confirm)|approved?|go ahead|post (it|this)|publish (it|this)|"
                           r"sign (it|this)|send (it|this)|refund (it|them|the customer))\b", re.I)
ASK = re.compile(r"^\s*(\?|/ask\b|why\b|where does|where did|how did you|what did you)", re.I)
# When the founder isn't answering a question of ours, any question in the chat goes to the advisor.
QUESTION_LIKE = re.compile(r"(\?\s*$)|^\s*(what|which|how|why|where|when|who|can|could|should|would|is|are|do|does|"
                           r"explain|tell me)\b", re.I)


class BlockedDecision(PermissionError):
    pass


class Runtime:
    def __init__(self, settings: Settings, run_id: str, *, router: Router | None = None, search=None, fetch=None):
        self.settings = settings
        self.run_id = run_id
        store = RunStore(settings.runs_dir, run_id)
        router = router or Router.from_settings(settings)
        search = search or SearxSearch(settings.searxng_url, settings.search_timeout,
                                         settings.searxng_engines, settings.searxng_fallback_engines)
        fetch = fetch or PageFetcher(settings.fetch_timeout)
        self.ctx = Context(settings, router, store, search, fetch)
        CONTEXTS[run_id] = self.ctx
        self.graph = build_graph(sqlite_checkpointer(str(store.path("checkpoints.db"))))
        if self.ctx.dossier is not None:
            store.log.tags = {"project": self.ctx.dossier.data_mode}
        self.config = {"configurable": {"thread_id": run_id}, "recursion_limit": 1000}

    @classmethod
    def new(cls, settings: Settings, brief: str, data_mode: str = "real", **kw) -> "Runtime":
        run_id = new_run_id()
        store = RunStore(settings.runs_dir, run_id)
        store.save(Dossier(run_id=run_id, brief=brief.strip(), data_mode=data_mode))
        store.progress(f"run {run_id} created (mode {settings.run_mode}"
                       f"{', single-model' if settings.single_model else ''})")
        rt = cls(settings, run_id, **kw)
        with rt.ctx.bound():
            log_action("orchestrator", "start_project", mode=data_mode)
        return rt

    # ---- graph driving ----
    @property
    def dossier(self) -> Dossier:
        return self.ctx.dossier

    def started(self) -> bool:
        return bool(self.graph.get_state(self.config).values)

    def advance(self, resume=None) -> dict | None:
        """Run until the next founder pause (or the end). Returns the pending request, if any."""
        if not self.started():
            self.graph.invoke({"run_id": self.run_id, "next": FIRST_NODE, "pending": None, "answer": None}, self.config)
        else:
            self.graph.invoke(Command(resume=NO_ANSWER if resume is None else resume), self.config)
        return self.pending()

    def set_data_mode(self, mode: str) -> str:
        """"real": the founder will enter real results (labelled founder-reported); "practice": results are
        practice numbers (labelled MOCKED). Payments and publishing are MOCKED in both."""
        if mode not in {"real", "practice"}:
            raise ValueError("project mode must be real or practice")
        with self.ctx.bound():
            self.dossier.data_mode = mode
            self.ctx.save()
            self.ctx.store.log.tags = {"project": mode}
            log_action("orchestrator", "set_project_mode", mode=mode)
        return mode

    def set_brand(self, brand: dict) -> dict:
        """The founder's brand for the landing page (name, colour, logo). Validated; the logo is never logged."""
        from .landing import clean_brand
        clean = clean_brand(brand)
        with self.ctx.bound():
            self.dossier.brand = clean
            self.ctx.save()
            log_action("founder", "set_brand", name=clean.get("name"), color=clean.get("color"),
                       logo=bool(clean.get("logo")))
        return clean

    def interrupted(self) -> bool:
        """Started, not finished, and not waiting for the founder: a step was cut off (e.g. the app restarted)."""
        return self.started() and not self.finished() and self.pending() is None

    def continue_interrupted(self) -> dict | None:
        """Re-run the step that was cut off, from the last checkpoint. Does nothing if the run is not interrupted."""
        if self.interrupted():
            self.graph.invoke(None, self.config)
        return self.pending()

    def pending(self) -> dict | None:
        snap = self.graph.get_state(self.config)
        for task in snap.tasks:
            for intr in task.interrupts:
                return intr.value
        return None

    def finished(self) -> bool:
        snap = self.graph.get_state(self.config)
        return self.started() and not snap.next

    # ---- founder actions ----
    def reply(self, text: str) -> dict:
        """Free text from the chat panel or the CLI prompt. Returns {'liaison': msg} or {'resumed': True}."""
        p = self.pending()
        with self.ctx.bound():
            self.ctx.store.converse("founder", text, phase=self.dossier.phase, kind="chat")
            answering = bool(p and p["kind"] in QUESTION_KINDS)
            if ASK.search(text) or (not answering and QUESTION_LIKE.search(text)):
                ans = self.ask(text.lstrip("?").removeprefix("/ask").strip())
                return {"liaison": ans}
            if CHAT_APPROVAL.search(text) and (not p or p["kind"] in {"approval", "gate"}):
                log_action("founder_liaison", "chat_approval_attempt_ignored", text=text[:120])
                msg = ("I can't take approvals in the chat. That's on purpose, so nothing goes out by accident. "
                       "Please use the buttons on screen (or `venture approve` / `venture gate` in the terminal), "
                       "so your decision is recorded properly.")
                self.ctx.say(msg)
                return {"liaison": msg}
            hit = self.ctx.guard.check(text, context="chat", subject="founder_message")
            if not hit.allowed:
                msg = (f"Sorry, I can't help with that ({hit.category.replace('_', ' ')}). To keep things honest, the "
                       "team never creates fake reviews or urgency, promises income, pretends to be someone else, moves "
                       "money, or builds products in excluded areas (physical goods, finance or crypto, health or legal "
                       "advice). Happy to help with anything else.")
                self.ctx.say(msg)
                return {"liaison": msg}
        if p and p["kind"] in QUESTION_KINDS:
            if p["kind"] == "readback":
                value = {"confirm": True} if CONFIRM.search(text) else {"correction": text}
            elif p["kind"] == "notes":
                value = {"text": text}
            else:
                value = text
            self.advance(value)
            return {"resumed": True}
        waiting = ({"approval": "for you to approve each item, or ask for changes, using the buttons",
                    "gate": "for your decision on this stage: pick one of the options on screen",
                    "mocked_input": "for the numbers in the form on screen"}.get(p["kind"], "for the next step")
                   if p else "for nothing right now: the team is working")
        msg = (f"Thanks, I've noted that and the team will take it into account. Right now I'm waiting {waiting}. "
               "If you have a question, just ask: I'll answer from what the team has found.")
        with self.ctx.bound():
            self.dossier.founder_inputs.append({"phase": self.dossier.phase, "type": "founder_reported", "text": text})
            self.ctx.save()
            self.ctx.say(msg)
        return {"liaison": msg}

    def skip(self) -> dict | None:
        """The founder skips a question or check-in. Same as no answer: anything it was about stays a labelled
        guess (ASSUMPTION). Never used for approvals or stage decisions."""
        p = self.pending()
        if not p or p["kind"] not in QUESTION_KINDS:
            raise ValueError("there is no question to skip right now")
        with self.ctx.bound():
            self.ctx.store.converse("founder", "(skipped)", phase=self.dossier.phase, kind="chat")
            log_action("founder", "skip_question", kind=p["kind"], field=p.get("field"))
        return self.advance(None)

    def timeout(self) -> dict | None:
        """Demo mode: an unanswered question times out; the gap stays a labelled assumption."""
        p = self.pending()
        if p and p["kind"] in QUESTION_KINDS and p.get("timeout"):
            return self.advance(None)
        return p

    def submit_input(self, values: dict) -> dict | None:
        p = self.pending()
        if not p or p["kind"] != "mocked_input":
            raise ValueError("no form is waiting")
        clean = {}
        for f in p["fields"]:
            v = values.get(f["name"], f.get("default"))
            if f["type"] == "number":
                v = float(v)
                if v < 0:
                    raise ValueError(f"{f['name']} must be ≥ 0")
            elif f["type"] == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in {"1", "true", "yes", "y"}
            clean[f["name"]] = v
        return self.advance(clean)

    def _resume_if_items_decided(self) -> dict | None:
        p = self.pending()
        if p and p["kind"] == "approval":
            statuses = [self.ctx.outbox.get(i["id"])["status"] for i in p["items"]]
            if all(s in {"approved", "rejected"} for s in statuses):
                return self.advance({"decided": True})
        return p

    def approve(self, item_id: str, confirmation: FounderConfirmation) -> dict | None:
        with self.ctx.bound():
            self.ctx.outbox.approve(item_id, confirmation)
        return self._resume_if_items_decided()

    def reject(self, item_id: str, note: str, channel: str) -> dict | None:
        with self.ctx.bound():
            self.ctx.outbox.reject(item_id, note, channel)
        return self._resume_if_items_decided()

    def decide_gate(self, decision: str, confirmation: FounderConfirmation, *, variant_id: str | None = None,
                    note: str = "") -> dict | None:
        p = self.pending()
        if not p or p["kind"] != "gate":
            raise ValueError("no gate decision is waiting")
        if decision not in {"go", "repeat", "pivot", "stop"}:
            raise ValueError("decision must be go, repeat, pivot or stop")
        crit = [a.field for a in self.ctx.assumptions.critical_unconfirmed(f"gate{p['gate']}")]
        if decision == "go" and crit:
            raise BlockedDecision("This GO rests on decision-critical assumptions you haven't confirmed: "
                                  + ", ".join(crit) + ". Confirm or correct them first (`venture confirm`).")
        with self.ctx.bound():
            self.ctx.outbox.record_gate_decision(gate=p["gate"], attempt=p["attempt"], decision=decision,
                                                 recommended=p["recommended"], confirmation=confirmation,
                                                 variant_id=variant_id, note=note)
        return self.advance({"gate": p["gate"]})

    def confirm_assumption(self, field: str, value: str | None = None) -> dict:
        """Confirm (same value) or correct (new value) an assumption. A correction while a gate waits re-runs
        only the dependent steps and evaluates the gate again."""
        p = self.pending()
        with self.ctx.bound():
            a = self.ctx.assumptions.by_field(field)
            if a is None:
                raise ValueError(f"no assumption about {field}")
            if value is None or str(value) == a.value:
                self.ctx.assumptions.confirm(field, a.value, answer="confirmed by command")
                log_action("founder", "confirm_assumption", approved_by="founder", field=field)
                return {"confirmed": field}
            from .departments.discover import _apply
            deps = _apply(self.ctx, field, str(value), correction=True)
            log_action("founder", "correct_assumption", approved_by="founder", field=field, value=str(value))
            self.ctx.save()
        target = "p1_score" if "p1_score" in deps else ("p1_gate" if "gate1" in deps else None)
        if p and p["kind"] == "gate" and target:
            self.advance({"rerun": target})
            return {"corrected": field, "rerun": target}
        return {"corrected": field}

    def ask(self, question: str) -> str:
        record = self.record_index()
        with self.ctx.bound():
            ans = founder_liaison.answer_from_record(self.ctx, question, record)
            text = ans.answer
            sources = []
            for i in ans.cited_ids:
                item = self.ctx.ledger.items.get(i)
                url = getattr(item, "url", None) if item else None
                host = urlparse(url).netloc.removeprefix("www.") if url else ""
                if host and host not in sources:
                    sources.append(host)
                # Never show internal ids: name the source instead (the prompt asks for this; the code makes sure).
                text = re.sub(rf"\[?\b{re.escape(i)}\b\]?", host or "the project records", text)
            if sources:
                text += "\n\nSources: " + ", ".join(sources[:5])
            self.ctx.say(text, kind="answer")
        return text

    def record_index(self) -> dict:
        """What 'ask anything' may use: dossier fields, evidence, assumptions and log entries, by ID."""
        d = self.dossier
        rec: dict[str, str] = {}
        for field in ("profile", "chosen_variant", "variants", "scores", "locked_targets", "lock_hash", "pricing",
                      "metrics", "signups", "lint"):
            val = getattr(d, field)
            if val not in (None, [], {}):
                rec[f"dossier.{field}"] = str(d.slice(field).get(field))[:800]
        for n, g in d.gates.items():
            rec[f"dossier.gates.{n}"] = f"{g.label}: {'; '.join(g.reasons)}"[:600]
        for e in list(self.ctx.ledger.items.values())[-40:]:
            rec[e.id] = f"{e.type} {e.url or ''}: {e.snippet[:200]}"
        for a in self.ctx.assumptions.items.values():
            rec[a.id] = f"{a.field} = {a.value} ({a.status})"
        for entry in self.ctx.store.log.entries[-60:]:
            rec[entry["id"]] = f"{entry['agent']} {entry['action']} model={entry['model']}"
        return rec

    # ---- status and export ----
    def status(self) -> dict:
        d = self.dossier
        return {
            "run_id": self.run_id, "phase": d.phase, "status": d.status if not self.finished() or d.status != "running" else "finished",
            "mode": self.settings.run_mode, "single_model": self.settings.single_model,
            "data_mode": self.dossier.data_mode,
            "pending": self.pending(),
            "gates": {n: {"outcome": g.outcome, "label": g.label, "reasons": g.reasons} for n, g in d.gates.items()},
            "outbox": [{k: v for k, v in i.items() if k != "content"} | {"hash": i["content_hash"].removeprefix("sha256:")[:12]}
                       for i in self.ctx.outbox.items()],
            "outputs": sorted(p.name for p in self.ctx.store.outputs.iterdir()),
            "labels": {"payments": "MOCKED", "publishing": "MOCKED", "event_log": "MOCKED",
                       "emails": "MOCKED", "legal_review": "STUBBED", "web_tool_browser_test": "STUBBED"},
        }


def export_run(settings: Settings, run_id: str, *, tag: str = "", clear: bool = True,
               outputs_dir: Path = ROOT / "outputs", log_path: Path = ROOT / "decision_log.json") -> list[str]:
    """Copy one run's outputs and decision log to the repository root (the committed demo run).
    With `tag`, file names get it after the step or gate part (phase1_gate_<tag>_decision.md) and the log
    entries are appended to the existing decision_log.json."""
    import json

    src = RunStore(settings.runs_dir, run_id)
    outputs_dir.mkdir(exist_ok=True)
    if clear:
        for old in outputs_dir.iterdir():
            if old.name != ".gitkeep" and old.is_file():
                old.unlink()
    copied = []
    for f in sorted(src.outputs.iterdir()):
        name = f.name
        if tag:
            name = re.sub(r"^(phase\d+_(?:step\d+|gate))_", rf"\1_{tag}_", name)
            if name == f.name:
                name = f"{tag}_{name}"
        shutil.copy(f, outputs_dir / name)
        copied.append(name)
    entries = json.loads(src.path("decision_log.json").read_text())
    for e in entries:
        e["run_id"] = run_id
    existing = json.loads(log_path.read_text()) if (not clear and log_path.exists()) else []
    log_path.write_text(json.dumps(existing + entries, indent=1))
    return copied
