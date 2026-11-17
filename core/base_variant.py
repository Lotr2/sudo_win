"""BaseVariant: the contract every architecture variant runs under.

A variant decides HOW to work (roles, order, models per role, retries, verifier use, context budgets).
It may not decide WHAT is written or how approvals, guardrails, sources, outbox, state and logging work:
those are the final helpers below, and __init_subclass__ refuses any subclass that overrides them.

Every step is saved to state.json as soon as it completes; a resumed run returns saved steps instead of
calling the model again, and outward actions run at most once per idea + action id.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType

import yaml

import gates
import guardrails
import payments
from approval import ApprovalBroker, ApprovalPending, payload_hash
from core import code_steps, render
from core.brief import Brief
from core.conventions import (LLM_STEP_KEYS, MAX_OUTPUT_BYTES, PAYMENT_TEST_LOG, STEP_BY_KEY, Step,
                              gate_filename, is_valid_output_name, steps_in_phase, waves)
from core.llm import LLMError
from core.log import DecisionLog
from core.outbox import DuplicateOutward, key_path, mark_superseded, write_outward
from core.parse import DELIM, ParseError, estimate_tokens, parse_answer, parse_claims
from core.state import RunState
from sources import Doc, SourceRegistry, check_provenance

OUTWARD_KIND = {"send_outreach": "send", "send_support_replies": "send", "publish_landing_page": "publish",
                "publish_posts": "publish", "open_store": "open_store"}
CONFIG_KEYS = ("models", "retries", "verifier", "budgets", "sampling", "seed", "digest_chars",
               "max_phase_retries", "max_parallel_steps")
FINAL_METHODS = ("__init__", "execute", "llm_step", "code_step", "run_step", "parallel", "waves", "reset_phase",
                 "phase_gate", "review_phase", "retry_phase", "attempt", "verify", "digest", "write_output", "outward",
                 "request_approval", "steps", "result", "pivot_target", "log")
DONE_STATUSES = ("completed", "stopped", "refused", "pivot")


class ContractError(TypeError):
    pass


class StepFailed(RuntimeError):
    pass


@dataclass
class RunContext:
    run_id: str
    variant: str
    run_dir: Path
    outputs_dir: Path
    outbox_dir: Path
    log: DecisionLog
    llm: object
    broker: ApprovalBroker
    state: RunState
    fake: bool
    stats: dict = field(default_factory=lambda: {
        "llm_calls": 0, "tokens": 0, "json_retries": 0, "truncations": 0, "trimmed_prompts": 0,
        "cached_steps": 0, "phase_retries": 0, "failed_steps": [], "claims_total": 0, "claims_verified": 0,
        "guardrail_findings": 0, "blocked_outward": 0, "outputs": []})
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def emit(self, type_: str, **kw) -> None:
        with self._lock, open(self.run_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "type": type_, **kw}) + "\n")

    def bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] += n

    def add(self, key: str, item) -> None:
        with self._lock:
            if item not in self.stats[key]:
                self.stats[key].append(item)


@dataclass
class StepRecord:
    step: Step
    data: dict
    text: str
    file: str
    failed: bool = False
    claims: list = field(default_factory=list)
    outward_status: str | None = None
    findings: list = field(default_factory=list)      # guardrail rule names
    content: str = ""

    def to_state(self) -> dict:
        return {"data": self.data, "file": self.file, "failed": self.failed, "claims": self.claims,
                "outward_status": self.outward_status, "findings": self.findings, "content": self.content}


@dataclass
class GateDecision:
    phase: int
    recommended: str
    final: str                  # go / pivot / stop / repeat (gate 6) or a retry: revise / redo / reset
    file: str
    reasons: list
    attempt: int = 1
    choice: str = ""
    note: str = ""


@dataclass
class RunResult:
    status: str                 # completed / stopped / pivot / paused / failed / refused
    idea: str
    last_phase: int
    outcome: str | None = None
    next_brief: str | None = None
    notes: list = field(default_factory=list)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BaseVariant:
    HINTS: dict = {}            # variant-defined extra instructions per step key (added to every attempt)

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        bad = [m for m in FINAL_METHODS if m in cls.__dict__]
        if bad:
            raise ContractError(f"{cls.__name__} overrides shared-core methods {bad}")

    # ------------------------------------------------------------------ setup
    def __init__(self, ctx: RunContext, variant_dir: Path):
        self.ctx = ctx
        self.dir = Path(variant_dir)
        self.config = yaml.safe_load((self.dir / "config.yaml").read_text(encoding="utf-8")) or {}
        missing = [k for k in CONFIG_KEYS if k not in self.config]
        if missing:
            raise ContractError(f"config.yaml missing keys {missing}")
        self.agents: dict[str, ModuleType] = {}
        self.prompts: dict[str, str] = {}
        self.schemas: dict[str, dict] = {}
        for f in sorted((self.dir / "agents").glob("*.py")):
            mod = _load_module(f, f"{ctx.variant}_agent_{f.stem}")
            if getattr(mod, "ROLE", None) != f.stem:
                raise ContractError(f"agents/{f.name} must define ROLE = {f.stem!r}")
            self.agents[f.stem] = mod
            self.prompts[f.stem] = (self.dir / "prompts" / f"{f.stem}.md").read_text(encoding="utf-8")
            self.schemas[f.stem] = json.loads((self.dir / "schemas" / f"{f.stem}.json").read_text(encoding="utf-8"))
        self.step_role: dict[str, str] = {}
        for role, mod in self.agents.items():
            for key in getattr(mod, "STEPS", []):
                if key in self.step_role:
                    raise ContractError(f"step {key} claimed by {self.step_role[key]} and {role}")
                self.step_role[key] = role
        uncovered = [k for k in LLM_STEP_KEYS if k not in self.step_role]
        if uncovered:
            raise ContractError(f"no agent covers LLM steps {uncovered}")
        self.reads = {k: tuple((getattr(self.agents[r], "READS", {}) or {}).get(k, STEP_BY_KEY[k].reads))
                      for k, r in self.step_role.items()}

    # ------------------------------------------------------------- entry point
    def run(self, brief: Brief, decisions: dict) -> RunResult:  # implemented by each variant
        raise NotImplementedError

    def execute(self, brief: Brief, decisions: dict) -> RunResult:
        """Called by the runner: restores saved state, screens the request, runs the variant safely."""
        self.brief, self.idea = brief, brief.id
        self.idea_decisions = (decisions or {}).get(brief.id) or {}
        self._rlock = threading.RLock()
        st = self.ctx.state.idea(self.idea)
        self.registry = SourceRegistry(offline=self.ctx.fake, idea=brief.id)
        for url, d in (st.get("sources") or {}).items():
            self.registry.docs[url] = Doc(url, d.get("title", ""), d.get("snippet", ""))
        cpath = self._commerce_path()
        self.commerce = payments.CommerceState.load(cpath) if cpath.exists() else payments.CommerceState()
        self.invariants: dict[str, bool] = dict(st.get("invariants") or {})
        self.results: dict[str, StepRecord] = {}
        self.revisions: dict[str, dict] = dict(st.get("revision") or {})
        for key, rec in (st.get("steps") or {}).items():
            if not rec.get("failed"):
                self.results[key] = self._restore(key, rec)
        self.ctx.state.set_idea(self.idea, status="running", brief_path=brief.path)
        self.ctx.emit("status", idea=self.idea, status="started", resumed=bool(self.results))
        if st.get("screen") != "accepted":
            screen = guardrails.screen_request(brief.text)
            reasons = screen.reasons
            if screen.allowed and "request" in self._verifier_points():
                ok, why = self.verify("request", brief.text)
                if not ok:
                    reasons = [f"verifier: {why}"]
            if reasons:
                self.log("request_screen", "refused_request", reasons=reasons)
                return self._finish(RunResult("refused", self.idea, 0, notes=reasons))
            self.log("request_screen", "accepted_request")
            self.ctx.state.set_idea(self.idea, screen="accepted")
        try:
            result = self.run(brief, decisions)
        except ApprovalPending as exc:
            result = RunResult("paused", self.idea, self._last_phase(), notes=[str(exc)])
        except Exception as exc:  # contract violations and crashes are reported, not hidden
            self.log("orchestrator", "run_failed", error=f"{type(exc).__name__}: {exc}")
            result = RunResult("failed", self.idea, self._last_phase(), notes=[f"{type(exc).__name__}: {exc}"])
        if not isinstance(result, RunResult):
            raise ContractError("run() must return RunResult")
        return self._finish(result)

    def _finish(self, result: RunResult) -> RunResult:
        self.commerce.save(self._commerce_path())
        self.ctx.state.set_idea(self.idea, status=result.status, result=asdict(result))
        self.ctx.emit("status", idea=self.idea, status=result.status, outcome=result.outcome)
        return result

    # ---------------------------------------------------------------- helpers
    def steps(self, phase: int) -> list[Step]:
        return steps_in_phase(phase)

    def waves(self, phase: int) -> list[list[Step]]:
        """This phase's steps grouped so each group only depends on earlier groups (run a group in parallel)."""
        return waves(phase, self.reads)

    def result(self, status: str, phase: int, outcome: str | None = None, **kw) -> RunResult:
        return RunResult(status, self.idea, phase, outcome, **kw)

    def pivot_target(self) -> str | None:
        return self.idea_decisions.get("pivot_to")

    def log(self, agent: str, action: str, **kw) -> dict:
        return self.ctx.log.append(agent, action, idea=getattr(self, "idea", None), **kw)

    def _commerce_path(self) -> Path:
        return self.ctx.run_dir / f"commerce_{self.idea}.json"

    def _last_phase(self) -> int:
        return max((r.step.phase for r in getattr(self, "results", {}).values()), default=0)

    def _verifier_points(self) -> list:
        v = self.config.get("verifier") or {}
        return list(v.get("points", [])) if v.get("enabled") else []

    def _model(self, role: str) -> str:
        m = self.config["models"]
        return (m.get("roles") or {}).get(role) or m["default"]

    def _sampling(self, role: str) -> dict:
        """Variant sampling (default, then per role) on top of the model's recommended values (config/models.yaml)."""
        s = self.config.get("sampling") or {}
        return {**(s.get("default") or {}), **((s.get("roles") or {}).get(role) or {})}

    def attempt(self, phase: int) -> int:
        """Current attempt of a phase (1 = first run; every revise/redo/reset adds one)."""
        return self.ctx.state.attempt(self.idea, phase)

    def _seed(self, phase: int | None) -> int:
        """A new attempt samples differently (same seed + same input would reproduce the same answer)."""
        return int(self.config["seed"]) + (1000 * (self.attempt(phase) - 1) if phase else 0)

    def _budget(self, role: str) -> tuple[int, int]:
        b = self.config["budgets"]
        d = dict(b.get("default") or {})
        d.update((b.get("roles") or {}).get(role) or {})
        return int(d.get("max_prompt_tokens", 5000)), int(d.get("max_output_tokens", 2000))

    def _restore(self, key: str, rec: dict) -> StepRecord:
        f = self.ctx.outputs_dir / rec["file"]
        text = f.read_text(encoding="utf-8") if f.exists() else ""
        return StepRecord(STEP_BY_KEY[key], rec["data"], text, rec["file"], False, rec.get("claims", []),
                          rec.get("outward_status"), rec.get("findings", []), rec.get("content", ""))

    def _save(self, rec: StepRecord) -> None:
        with self._rlock:
            if not rec.failed:
                self.results[rec.step.key] = rec
        self.ctx.state.put_step(self.idea, rec.step.key, rec.to_state())

    def _cached(self, key: str) -> StepRecord | None:
        rec = self.results.get(key)
        if rec:
            self.ctx.bump("cached_steps")
            self.ctx.emit("step", idea=self.idea, phase=rec.step.phase, step=key, status="done", file=rec.file, cached=True)
        return rec

    def reset_phase(self, phase: int) -> None:
        """Forget a phase's steps and gate (for a 'repeat'). Outward actions already done stay done."""
        with self._rlock:
            for k in [k for k in self.results if k.startswith(f"phase{phase}_")]:
                del self.results[k]
        self.ctx.state.drop_phase(self.idea, phase)

    def digest(self, step_keys) -> str:
        """Compact inputs for the next step: each earlier step's metadata as one JSON line + a content excerpt."""
        return "\n\n".join(self._digest_parts(step_keys))

    def _digest_parts(self, step_keys) -> list[str]:
        n = int(self.config.get("digest_chars", 800))
        parts = []
        for k in step_keys:
            r = self.results.get(k)
            if not r or r.failed:
                continue
            meta = {"step": r.step.title, **{a: b for a, b in r.data.items() if a not in ("claims",)}}
            if r.claims:
                meta["claims"] = [c["text"] + (f" [source: {c['source_url']}]" if c["source_url"] else " [ASSUMPTION]")
                                  for c in r.claims[:4]]
            excerpt = f"\n{r.content[:n]}" if r.content else ""
            parts.append(json.dumps(meta, ensure_ascii=False)[: 3 * n] + excerpt)
        return parts

    def write_output(self, name: str, text: str) -> str:
        if not is_valid_output_name(name):
            raise ContractError(f"non-standard output name {name}")
        if name != PAYMENT_TEST_LOG and f"_{self.idea}-" not in name:
            raise ContractError(f"output {name} is not for idea {self.idea}")
        data = text.encode("utf-8")
        if len(data) > MAX_OUTPUT_BYTES:
            text = data[:MAX_OUTPUT_BYTES - 200].decode("utf-8", "ignore") + "\n\n[TRUNCATED: output size limit]\n"
        (self.ctx.outputs_dir / name).write_text(text, encoding="utf-8")
        self.ctx.add("outputs", name)
        return name

    # --------------------------------------------------------------- LLM work
    def _format_block(self, schema: dict, content: bool) -> str:
        if not content:
            return ("OUTPUT FORMAT: answer with ONE flat JSON object only (strings, numbers, lists of strings; "
                    "no nested objects, no code).\nSCHEMA: " + json.dumps(schema, separators=(",", ":")))
        return ("OUTPUT FORMAT:\n1. ONE flat JSON object (strings, numbers, lists of strings; no nested objects, never code).\n"
                "SCHEMA: " + json.dumps(schema, separators=(",", ":")) + "\n"
                "Claims are strings ending in \"[source: <URL from SOURCES>]\" or \"[ASSUMPTION]\".\n"
                f"2. Then a line {DELIM} and the content in Markdown. Any code goes only here, never inside the JSON.")

    def _call(self, role: str, system: str, prompt: str, schema: dict, content: bool, phase: int | None = None) -> dict:
        """One model answer, parsed. Truncation fails the step; a parse error is retried with the error fed back."""
        model = self._model(role)
        _max_prompt, max_out = self._budget(role)
        msg, last = prompt, None
        for _attempt in range(int(self.config["retries"]) + 1):
            try:
                res = self.ctx.llm.complete(model=model, system=system, prompt=msg, max_tokens=max_out,
                                            sampling=self._sampling(role), seed=self._seed(phase),
                                            json_schema=None if content else schema)
            except LLMError as exc:
                raise StepFailed(f"model unavailable: {exc}") from exc
            self.ctx.bump("llm_calls")
            self.ctx.bump("tokens", res.tokens)
            if res.finish_reason == "length":
                self.ctx.bump("truncations")
                raise StepFailed(f"output truncated at {max_out} tokens (finish_reason=length)")
            try:
                data, body = parse_answer(res.text, schema, content)
                return {"data": data, "content": body, "model": res.model, "endpoint": res.endpoint, "tokens": res.tokens}
            except ParseError as exc:
                last = exc
                self.ctx.bump("json_retries")
                msg = prompt + f"\n\nYOUR PREVIOUS ANSWER COULD NOT BE USED: {exc}. Answer again in the required format."
        raise StepFailed(f"invalid answer after {int(self.config['retries']) + 1} attempts: {last}")

    def _ask(self, role: str, step: Step, task: str, reads, extra: str = "") -> dict:
        """Build a budgeted prompt (trim sources, then oldest inputs) and call the model."""
        schema = self.schemas[role]
        system = self.prompts[role]
        max_prompt, _ = self._budget(role)
        head = f"TASK ({step.title}): {task}\n\nFOUNDER BRIEF:\n{self.brief.short()}\n{extra}\n"
        fmt = self._format_block(schema, step.content)
        digest_parts = self._digest_parts(reads) if reads else []
        sources = [f"- {d.url} | {d.title} | {d.snippet[:200]}" for d in list(self.registry.docs.values())[:12]]

        def assemble() -> str:
            return (head + "\nINPUTS:\n" + ("\n\n".join(digest_parts) or "(none)") +
                    "\n\nSOURCES (cite only these URLs):\n" + ("\n".join(sources) or "(none)") + "\n\n" + fmt)

        prompt = assemble()
        trimmed = False
        while estimate_tokens(system + prompt) > max_prompt and (sources or digest_parts):
            trimmed = True
            if len(sources) > 3:
                sources.pop()
            elif digest_parts:
                digest_parts.pop(0)
            else:
                sources.pop()
            prompt = assemble()
        if trimmed:
            self.ctx.bump("trimmed_prompts")
        if estimate_tokens(system + prompt) > max_prompt:
            raise StepFailed(f"prompt over budget ({max_prompt} tokens) even without inputs")
        return self._call(role, system, prompt, schema, step.content, step.phase)

    def verify(self, kind: str, text: str, evidence: str = "") -> tuple[bool, str]:
        """Narrow yes/no check by the verifier role. It only blocks or downgrades, and fails closed on errors."""
        if "verifier" not in self.agents or kind not in self._verifier_points():
            return True, "verifier off"
        questions = {
            "request": "Does this request ask for anything the system must never do (fake reviews/sign-ups/urgency, income promises, impersonation, moving money, storing credentials, paywalling, or an excluded product)? supported=true means the request is SAFE.",
            "claim": "Does the EVIDENCE snippet support the CLAIM? supported=true only if it clearly does.",
            "outward": "Is this text free of income/success promises, fake social proof, urgency and impersonation? supported=true means it is SAFE to publish.",
        }
        schema = self.schemas["verifier"]
        prompt = (f"QUESTION: {questions[kind]}\n\nTEXT:\n{text[:3000]}\n\nEVIDENCE:\n{evidence[:1500]}\n\n"
                  + self._format_block(schema, False))
        try:
            out = self._call("verifier", self.prompts["verifier"], prompt, schema, False)
        except StepFailed as exc:
            self.log("verifier", f"verify_{kind}_error", model=self._model("verifier"), error=str(exc), result="fail_closed")
            return False, f"verifier unavailable ({exc}); failing closed"
        ok, why = bool(out["data"].get("supported")), str(out["data"].get("reason", ""))
        self.log("verifier", f"verify_{kind}", model=out["model"], tokens=out["tokens"], supported=ok,
                 endpoint=out["endpoint"])
        return ok, why

    def _fail(self, step: Step, role: str, err: str) -> StepRecord:
        meta = {"agent": role, "model": self._model(role), "run_id": self.ctx.run_id, "variant": self.ctx.variant}
        text = render.render_md(step, self.idea, {"title": "STEP FAILED", "summary": err}, [], meta)
        if step.ext == "html":
            text = f"<!doctype html><p>STEP FAILED: {err}</p>"
        self.write_output(step.filename(self.idea), text)
        self.ctx.add("failed_steps", f"{self.idea}:{step.key}")
        self.log(role, f"{step.key}:{step.short}", model=self._model(role), failed=True, error=err[:300])
        rec = StepRecord(step, {"error": err[:500]}, text, step.filename(self.idea), failed=True)
        self._save(rec)
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="failed", error=err[:300])
        return rec

    def _fetch(self, role: str, step: Step) -> None:
        query = (getattr(self.agents[role], "SOURCES", {}) or {}).get(step.key, step.sources_query)
        if not query:
            return
        docs = self.registry.fetch(query.format(idea=self.brief.idea, market=self.brief.target_market or self.brief.idea))
        self.ctx.state.set_idea(self.idea, sources={u: {"title": d.title, "snippet": d.snippet}
                                                     for u, d in self.registry.docs.items()})
        self.log(role, f"fetch_sources:{step.key}", sources=[d.url for d in docs], query=query,
                 errors=self.registry.errors[-2:])

    def _features(self) -> list[str]:
        spec = self.results.get("phase3_step1")
        return [str(f) for f in (spec.data.get("features") or [])] if spec else []

    def llm_step(self, step_key: str, role: str | None = None, extra_inputs: str = "") -> StepRecord:
        step = STEP_BY_KEY[step_key]
        if step.kind != "llm":
            raise ContractError(f"{step_key} is a code step")
        cached = self._cached(step_key)
        if cached:
            if step.outward and cached.outward_status == "pending":
                cached.outward_status = self.outward(cached)
                self._save(cached)
            return cached
        role = role or self.step_role[step_key]
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="running", role=role)
        reads = self.reads.get(step_key, step.reads)
        try:
            self._fetch(role, step)
            extra_inputs = extra_inputs + self.HINTS.get(step_key, "") + self._revision_block(step_key)
            if step.fanout == "features":
                data, content, model, tokens = self._fanout_features(role, step, reads, extra_inputs)
            else:
                extra = extra_inputs
                if step_key == "phase3_step3" and self._features():
                    extra += ("\nFEATURES TO COVER (one section each, feature name exactly in the heading): "
                              + "; ".join(self._features()))
                out = self._ask(role, step, step.task, reads, extra)
                data, content, model, tokens = out["data"], out["content"], out["model"], out["tokens"]
        except StepFailed as exc:
            return self._fail(step, role, str(exc))
        claims, verified, total = check_provenance(parse_claims(data.get("claims")), self.registry)
        if "claims" in self._verifier_points():
            for c in [c for c in claims if c["source_url"]][:3]:
                ok, _ = self.verify("claim", c["text"], self.registry.docs[c["source_url"]].snippet)
                if not ok:
                    c.update(source_url=None, label="ASSUMPTION", dropped_url=c["source_url"])
                    verified -= 1
        self.ctx.bump("claims_total", total)
        self.ctx.bump("claims_verified", verified)
        meta = {"agent": role, "model": model, "run_id": self.ctx.run_id, "variant": self.ctx.variant}
        if step.ext == "html":
            price_rec = self.results.get("phase4_step1")
            text = render.render_page(step, self.idea, data, meta, price=price_rec.data.get("price_usd") if price_rec else None)
        else:
            text = render.render_md(step, self.idea, data, claims, meta, content)
        findings = [f.rule for f in guardrails.lint(text)]
        self.ctx.bump("guardrail_findings", len(findings))
        name = self.write_output(step.filename(self.idea), text)
        self.log(role, f"{step_key}:{step.short}", sources=[c["source_url"] for c in claims if c["source_url"]],
                 model=model, tokens=tokens, claims_verified=verified, claims_total=total, guardrail_findings=findings)
        rec = StepRecord(step, data, text, name, claims=claims, findings=findings, content=content,
                         outward_status="pending" if step.outward else None)
        self._save(rec)                       # saved before the outward action: a crash cannot lose or repeat it
        if step.outward:
            rec.outward_status = self.outward(rec)
            self._save(rec)
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="done", file=name,
                      outward=rec.outward_status)
        return rec

    def _fanout_features(self, role: str, step: Step, reads, extra: str = "") -> tuple[dict, str, str, int]:
        """One call per spec feature (in parallel); code is saved as plain files under product/.

        On a 'revise', only the features named by the gate are re-run; the others keep their saved content.
        """
        feats = self._features()
        if not feats:
            raise StepFailed("the MVP spec lists no features")
        product = self.ctx.run_dir / "product" / self.idea
        only = (self.revisions.get(step.key) or {}).get("features")
        kept = {f: (product / self._slug(f)).read_text(encoding="utf-8") for f in feats
                if only and f not in only and (product / self._slug(f)).exists()}

        def one(f: str) -> dict:
            return self._ask(role, step, step.task, reads, extra=f"{extra}\nFEATURE: {f}")

        todo = [f for f in feats if f not in kept]
        with ThreadPoolExecutor(max_workers=self._pool_size(len(todo))) as ex:
            futures = {f: ex.submit(one, f) for f in todo}
        outs, errors = {f: {"content": c, "data": {"claims": []}, "model": "kept", "tokens": 0} for f, c in kept.items()}, []
        for f, fut in futures.items():
            try:
                outs[f] = fut.result()
            except StepFailed as exc:
                errors.append(f"{f}: {exc}")
        if errors:
            raise StepFailed("feature calls failed: " + "; ".join(errors))
        product.mkdir(parents=True, exist_ok=True)
        sections, claims = [], []
        for f in feats:
            body = outs[f]["content"].strip()
            (product / self._slug(f)).write_text(body + "\n", encoding="utf-8")
            sections.append(body if body.lstrip().startswith("#") and f.lower() in body.lower()[:200] else f"## {f}\n\n{body}")
            claims += outs[f]["data"].get("claims") or []
        data = {"title": f"MVP: {len(feats)} features", "summary": f"Built one feature per call: {', '.join(feats)}.",
                "features": feats, "claims": claims}
        model = next((o["model"] for o in outs.values() if o["model"] != "kept"), self._model(role))
        return data, "\n\n".join(sections), model, sum(o["tokens"] for o in outs.values())

    @staticmethod
    def _slug(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + ".md"

    def code_step(self, step_key: str) -> StepRecord:
        step = STEP_BY_KEY[step_key]
        if step.kind != "code":
            raise ContractError(f"{step_key} is an LLM step")
        cached = self._cached(step_key)
        if cached:
            return cached
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="running", role="code")
        data, text = code_steps.run(self, step)
        name = self.write_output(step.filename(self.idea), text)
        failed = bool(data.get("failed"))
        if failed:
            self.ctx.add("failed_steps", f"{self.idea}:{step_key}")
        self.log(code_steps.AGENT.get(step_key, "code"), f"{step_key}:{step.short}", failed=failed)
        if step.phase == 5:
            self.commerce.save(self._commerce_path())
            self.ctx.state.set_idea(self.idea, invariants=self.invariants)
        rec = StepRecord(step, data, text, name, failed=failed)
        self._save(rec)
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="failed" if failed else "done", file=name)
        return rec

    def run_step(self, step_key: str) -> StepRecord:
        return self.llm_step(step_key) if STEP_BY_KEY[step_key].kind == "llm" else self.code_step(step_key)

    def _pool_size(self, n: int) -> int:
        router = getattr(self.ctx.llm, "router", None)
        cap = router.capacity if router else 1
        return max(1, min(n, int(self.config["max_parallel_steps"]), cap))

    def parallel(self, step_keys: list[str]) -> list[StepRecord]:
        """Run independent steps at once (bounded by max_parallel_steps and free model slots)."""
        if len(step_keys) == 1:
            return [self.run_step(step_keys[0])]
        with ThreadPoolExecutor(max_workers=self._pool_size(len(step_keys))) as ex:
            futures = [ex.submit(self.run_step, k) for k in step_keys]
        results, pending = [], None
        for fut in futures:
            try:
                results.append(fut.result())
            except ApprovalPending as exc:
                pending = pending or exc
        if pending:
            raise pending
        return results

    # ----------------------------------------------------- approvals & outward
    def request_approval(self, action_id: str, kind: str, summary: str, content: str, phase: int | None = None):
        attempt = self.attempt(phase) if phase else 1
        self.ctx.emit("approval", idea=self.idea, action_id=action_id, kind=kind, status="pending", attempt=attempt)
        r = self.ctx.broker.request(self.idea, action_id, kind, summary, content, attempt)
        self.ctx.emit("approval", idea=self.idea, action_id=action_id, kind=kind, attempt=attempt,
                      status="approved" if r else "rejected")
        return r

    def outward(self, rec: StepRecord) -> str:
        """Publish / send / open the store once per content. Changed content (after revise, redo or reset)
        needs a new founder approval for that attempt; the earlier outbox item is marked SUPERSEDED, not deleted."""
        step = rec.step
        content_hash = payload_hash(rec.text)
        done = self.ctx.state.outward_done(self.idea, step.action_id)
        if done and done.get("payload_hash") == content_hash:
            return "approved"                                   # same content already went out: never twice
        kind = OUTWARD_KIND[step.outward]
        reasons = list(rec.findings)
        if not reasons and "outward" in self._verifier_points():
            ok, why = self.verify("outward", rec.text)
            if not ok:
                reasons = [f"verifier: {why}"]
        if reasons:
            self.ctx.bump("blocked_outward")
            self.log("guardrails", f"blocked:{step.action_id}", reasons=reasons)
            return "blocked"
        attempt = self.attempt(step.phase)
        what = "changed content, new approval needed" if done else step.outward
        receipt = self.request_approval(step.action_id, kind, f"{step.title}: {what} (MOCKED)", rec.text, step.phase)
        if receipt is None:
            return "rejected"
        key = f"{self.idea}__{step.action_id}" + (f"__a{attempt}" if attempt > 1 else "")
        try:
            if kind == "open_store":
                price_rec = self.results.get("phase4_step1")
                price = (price_rec.data.get("price_usd") if price_rec else None) or 0
                payments.open_store(self.commerce, self.brief.idea, price, content=rec.text, receipt=receipt)
                self.commerce.save(self._commerce_path())
                path = write_outward(self.ctx.outbox_dir, "publish", f"{self.idea}-store", rec.text, key)
            else:
                from core import outbox
                fn = outbox.publish if kind == "publish" else outbox.send
                path = fn(self.ctx.outbox_dir, f"{self.idea}-{step.short}", content=rec.text, key=key, receipt=receipt)
        except DuplicateOutward:
            path = key_path(self.ctx.outbox_dir, key)      # written before a crash; never write it again
        name = Path(path).name
        superseded = list((done or {}).get("superseded", []))
        if done and done.get("file") and done["file"] != name:
            mark_superseded(self.ctx.outbox_dir, done["file"], name, attempt)
            superseded.append(done["file"])
            self.log("outbox", f"supersede:{step.outward}", approved_by="founder", label="MOCKED",
                     superseded_file=done["file"], replaced_by=name, attempt=attempt, action_id=step.action_id)
        self.ctx.state.mark_outward(self.idea, step.action_id, file=name, payload_hash=content_hash, attempt=attempt,
                                    superseded=superseded)
        self.log("outbox", step.outward, approved_by="founder", label="MOCKED", outbox_file=name,
                 action_id=step.action_id, attempt=attempt)
        return "approved"

    # ------------------------------------------------------------------ gates and stage review
    def run_phase(self, phase: int) -> None:
        """Run a phase's steps: waves of independent steps in parallel. Variants may override this.
        Steps already done in this attempt are returned from state, so it also resumes and re-runs only what a
        revise/redo/reset cleared."""
        for wave in self.waves(phase):
            self.parallel([s.key for s in wave])

    def review_phase(self, phase: int) -> GateDecision:
        """Run the phase and its gate; apply the founder's revise / redo / reset and check again, until the
        founder decides go, pivot or stop (or 'repeat' at gate 6, which ends the run to collect real evidence)."""
        while True:
            self.run_phase(phase)
            d = self.phase_gate(phase)
            if d.final not in gates.RETRY_FINALS:
                return d
            self.retry_phase(phase, d)

    def phase_gate(self, phase: int) -> GateDecision:
        attempt = self.attempt(phase)
        saved = self.ctx.state.get_gate(self.idea, phase)
        if saved and int(saved.get("attempt", 1)) == attempt:
            return GateDecision(**saved)
        g = code_steps.gate_result(self, phase)
        name = gate_filename(phase, self.idea, g.outcome)
        for old in self.ctx.outputs_dir.glob(f"phase{phase}_gate_{self.idea}-*.md"):
            if old.name != name:                    # an earlier attempt's gate file (kept under attempts/)
                old.unlink()
        cap = int(self.config["max_phase_retries"])
        allowed = gates.choices(phase, g.outcome, attempt - 1 < cap)
        body = (code_steps.gate_text(self, g) + f"Attempt {attempt} of this phase; retries used {attempt - 1} of {cap}. "
                f"Founder options: {', '.join(c for c in allowed if c != 'repeat')}.\n")
        self.write_output(name, body)
        self.log("gate", f"phase{phase}_gate", outcome=g.outcome, reasons=g.reasons, attempt=attempt)
        self.ctx.emit("gate", idea=self.idea, phase=phase, outcome=g.outcome, file=name, status="awaiting_founder",
                      attempt=attempt)
        self.ctx.emit("approval", idea=self.idea, action_id=f"phase{phase}.gate", kind="gate", status="pending",
                      attempt=attempt)
        d = self.ctx.broker.decide(self.idea, f"phase{phase}.gate", "gate",
                                   f"Phase {phase} gate (attempt {attempt}) recommends {g.outcome.upper()}: "
                                   f"{'; '.join(g.reasons)}", body, attempt, allowed)
        final = gates.resolve(phase, d.choice, g.outcome)
        self.write_output(name, body + f"\n## Founder decision\n\n- Decision: **{final}**"
                          f"{' (accepted the recommendation)' if d.choice == 'accept' else ' (founder choice)'}\n"
                          f"- Input source: {d.source}\n" + (f"- Founder note: {d.note}\n" if d.note else ""))
        self.ctx.emit("gate", idea=self.idea, phase=phase, outcome=g.outcome, final=final, file=name,
                      status="decided", attempt=attempt)
        gd = GateDecision(phase, g.outcome, final, name, g.reasons, attempt, d.choice, d.note)
        self.ctx.state.put_gate(self.idea, phase, asdict(gd))
        self._snapshot(phase, gd, d.source)
        return gd

    def _snapshot(self, phase: int, gd: GateDecision, source: str) -> None:
        """Keep this attempt: outputs, product files (phase 3), commerce + payment log (phase 5), decision."""
        dest = self.ctx.run_dir / "attempts" / self.idea / f"phase{phase}" / str(gd.attempt)
        dest.mkdir(parents=True, exist_ok=True)
        for f in self.ctx.outputs_dir.glob(f"phase{phase}_*_{self.idea}-*"):
            shutil.copy2(f, dest / f.name)
        if phase == 3 and (self.ctx.run_dir / "product" / self.idea).exists():
            shutil.copytree(self.ctx.run_dir / "product" / self.idea, dest / "product", dirs_exist_ok=True)
        if phase == 5:
            if self._commerce_path().exists():
                shutil.copy2(self._commerce_path(), dest / "commerce.json")
            (dest / "payment_log.md").write_text("\n".join(self.ctx.state.idea(self.idea).get("payment_log") or []) + "\n",
                                                encoding="utf-8")
        steps = {k: {"failed": bool(r.get("failed")), "error": (r.get("data") or {}).get("error")}
                 for k, r in (self.ctx.state.idea(self.idea).get("steps") or {}).items() if k.startswith(f"phase{phase}_")}
        revised = sorted(k for k, r in self.revisions.items() if k.startswith(f"phase{phase}_") and r.get("attempt") == gd.attempt)
        (dest / "attempt.json").write_text(json.dumps({
            "idea": self.idea, "phase": phase, "attempt": gd.attempt, "recommended": gd.recommended,
            "passed": gd.recommended == "go", "reasons": gd.reasons, "choice": gd.choice, "final": gd.final,
            "note": gd.note, "input_source": source, "revised_steps": revised, "steps": steps, "at": time.time()},
            indent=1), encoding="utf-8")

    def _revision_block(self, step_key: str) -> str:
        r = self.revisions.get(step_key)
        if not r or r.get("attempt") != self.attempt(STEP_BY_KEY[step_key].phase):
            return ""
        lines = [f"\nREVISION (attempt {r['attempt']}): the previous attempt of this phase did not pass its gate "
                 "or the founder asked for changes."]
        if r.get("reasons"):
            lines += ["WHAT FAILED:"] + [f"- {x}" for x in r["reasons"]]
        if r.get("note"):
            lines.append(f"FOUNDER NOTE: {r['note']}")
        if r.get("previous"):
            lines.append("PREVIOUS ANSWER (compressed):\n" + r["previous"])
        lines.append("Fix what failed and follow the founder note; keep what was fine. "
                     "Do not return the previous answer unchanged.")
        return "\n".join(lines) + "\n"

    def _dependents(self, phase: int, keys: set) -> set:
        """Steps in this phase that read (directly or indirectly) one of `keys`."""
        steps = steps_in_phase(phase)
        deps = {s.key: set(self.reads.get(s.key, s.reads)) | set(s.after) for s in steps}
        out, changed = set(keys), True
        while changed:
            changed = False
            for s in steps:
                if s.key not in out and deps[s.key] & out:
                    out.add(s.key)
                    changed = True
        return out - set(keys)

    def retry_phase(self, phase: int, decision: GateDecision) -> None:
        """Apply the founder's revise / redo / reset for this phase. The caller then runs the phase and its gate
        again (review_phase does both). Each call is one retry and counts toward max_phase_retries."""
        if decision.final not in gates.RETRY_FINALS:
            raise ContractError(f"retry_phase needs revise/redo/reset, got {decision.final}")
        n = decision.attempt
        if self.attempt(phase) != n:
            return                                   # already applied before a crash (resumed run)
        limit = 2 * int(self.config.get("digest_chars", 800))
        plan: dict[str, dict] = {}
        if decision.final == "revise":
            targets = code_steps.revision_targets(self, phase, decision)
            for k in self._dependents(phase, set(targets)):
                targets[k] = {"reasons": [f"an input of this step was revised ({', '.join(sorted(targets))})"],
                              "features": None}
            keys = list(targets)
            for k, t in targets.items():
                prev = "\n\n".join(self._digest_parts([k]))
                if not prev:
                    err = ((self.ctx.state.get_step(self.idea, k) or {}).get("data") or {}).get("error")
                    prev = f"(the previous attempt of this step failed: {err})" if err else ""
                plan[k] = {"attempt": n + 1, "reasons": t["reasons"], "features": t.get("features"),
                           "note": decision.note, "previous": prev[:limit]}
        else:
            keys = [s.key for s in steps_in_phase(phase)]
            if decision.note:                        # a redo starts fresh, but the founder's note still applies
                plan = {k: {"attempt": n + 1, "reasons": [], "features": None, "note": decision.note, "previous": ""}
                        for k in keys if STEP_BY_KEY[k].kind == "llm"}
            if decision.final == "reset":            # phase 5: new MOCKED commerce state, new test orders
                self.commerce = payments.CommerceState()
                self._commerce_path().unlink(missing_ok=True)
                self.invariants = {}
                self.ctx.state.set_idea(self.idea, invariants={}, payment_log=[])
        with self._rlock:
            for k in keys:
                self.results.pop(k, None)
        self.revisions = {k: v for k, v in self.revisions.items() if not k.startswith(f"phase{phase}_")}
        self.revisions.update(plan)
        self.ctx.state.set_idea(self.idea, revision=self.revisions)
        self.ctx.state.drop_steps(self.idea, keys)
        self.ctx.state.set_attempt(self.idea, phase, n + 1)
        self.ctx.bump("phase_retries")
        self.log("orchestrator", f"{decision.final}:phase{phase}", attempt=n + 1, steps=sorted(keys),
                 note=decision.note or None)
        self.ctx.emit("retry", idea=self.idea, phase=phase, mode=decision.final, attempt=n + 1, steps=sorted(keys))
