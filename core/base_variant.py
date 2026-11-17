"""BaseVariant: the contract every architecture variant runs under.

A variant decides HOW to work (roles, order, retries, verifier use, context). It may not decide WHAT
is written or how approvals, guardrails, sources, outbox and logging work: those are the final
helpers below, and __init_subclass__ refuses any subclass that overrides them.
"""
from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import jsonschema
import yaml

import gates
import guardrails
import payments
from approval import ApprovalBroker, ApprovalPending
from core import code_steps, render
from core.brief import Brief
from core.conventions import (LLM_STEP_KEYS, MAX_OUTPUT_BYTES, PAYMENT_TEST_LOG, STEP_BY_KEY, Step,
                              gate_filename, is_valid_output_name, steps_in_phase)
from core.log import DecisionLog
from core.outbox import _write as _outbox_write
from sources import SourceRegistry, check_provenance

OUTWARD_KIND = {"send_outreach": "send", "send_support_replies": "send", "publish_landing_page": "publish",
                "publish_posts": "publish", "open_store": "open_store"}
CONFIG_KEYS = ("models", "retries", "verifier", "num_ctx", "temperature", "seed", "digest_chars", "max_phase_repeats")
FINAL_METHODS = ("__init__", "execute", "llm_step", "code_step", "phase_gate", "verify", "digest",
                 "write_output", "outward", "request_approval", "steps", "result", "pivot_target", "log")


class ContractError(TypeError):
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
    fake: bool
    stats: dict = field(default_factory=lambda: {
        "llm_calls": 0, "tokens": 0, "failed_steps": [], "claims_total": 0, "claims_verified": 0,
        "guardrail_findings": 0, "blocked_outward": 0, "outputs": []})

    def emit(self, type_: str, **kw) -> None:
        with open(self.run_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "type": type_, **kw}) + "\n")


@dataclass
class StepRecord:
    step: Step
    data: dict
    text: str
    file: str
    failed: bool = False
    claims: list = field(default_factory=list)
    outward_status: str | None = None
    findings: list = field(default_factory=list)


@dataclass
class GateDecision:
    phase: int
    recommended: str
    final: str
    file: str
    reasons: list


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

    # ------------------------------------------------------------- entry point
    def run(self, brief: Brief, decisions: dict) -> RunResult:  # implemented by each variant
        raise NotImplementedError

    def execute(self, brief: Brief, decisions: dict) -> RunResult:
        """Called by the runner: screens the request, sets per-idea state, runs the variant safely."""
        self.brief, self.idea = brief, brief.id
        self.idea_decisions = (decisions or {}).get(brief.id) or {}
        self.registry = SourceRegistry(offline=self.ctx.fake, idea=brief.id)
        self.commerce = payments.CommerceState()
        self.results: dict[str, StepRecord] = {}
        self.payment_log: list[str] = []
        self.invariants: dict[str, bool] = {}
        self.ctx.emit("status", idea=self.idea, status="started")
        screen = guardrails.screen_request(brief.text)
        reasons = screen.reasons
        if screen.allowed and "request" in self._verifier_points():
            ok, why = self.verify("request", brief.text)
            if not ok:
                reasons = [f"verifier: {why}"]
        if reasons:
            self.log("request_screen", "refused_request", reasons=reasons)
            self.ctx.emit("status", idea=self.idea, status="refused", reasons=reasons)
            return RunResult("refused", self.idea, 0, notes=reasons)
        self.log("request_screen", "accepted_request")
        try:
            result = self.run(brief, decisions)
        except ApprovalPending as exc:
            result = RunResult("paused", self.idea, self._last_phase(), notes=[str(exc)])
        except Exception as exc:  # contract violations and crashes are reported, not hidden
            self.log("orchestrator", "run_failed", error=f"{type(exc).__name__}: {exc}")
            result = RunResult("failed", self.idea, self._last_phase(), notes=[f"{type(exc).__name__}: {exc}"])
        if not isinstance(result, RunResult):
            raise ContractError("run() must return RunResult")
        self.commerce.save(self.ctx.run_dir / f"commerce_{self.idea}.json")
        self.ctx.emit("status", idea=self.idea, status=result.status, outcome=result.outcome)
        return result

    # ---------------------------------------------------------------- helpers
    def steps(self, phase: int) -> list[Step]:
        return steps_in_phase(phase)

    def result(self, status: str, phase: int, outcome: str | None = None, **kw) -> RunResult:
        return RunResult(status, self.idea, phase, outcome, **kw)

    def pivot_target(self) -> str | None:
        return self.idea_decisions.get("pivot_to")

    def log(self, agent: str, action: str, **kw) -> dict:
        return self.ctx.log.append(agent, action, idea=getattr(self, "idea", None), **kw)

    def _last_phase(self) -> int:
        return max((r.step.phase for r in getattr(self, "results", {}).values()), default=0)

    def _verifier_points(self) -> list:
        v = self.config.get("verifier") or {}
        return list(v.get("points", [])) if v.get("enabled") else []

    def _model(self, role: str) -> str:
        m = self.config["models"]
        return (m.get("roles") or {}).get(role) or m["default"]

    def digest(self, step_keys) -> str:
        n = int(self.config.get("digest_chars", 1500))
        parts = []
        for k in step_keys:
            r = self.results.get(k)
            if r and not r.failed:
                parts.append(f"### {r.step.title}\n{r.text[:n]}")
        return "\n\n".join(parts) or "(no earlier outputs)"

    def write_output(self, name: str, text: str) -> str:
        if not is_valid_output_name(name):
            raise ContractError(f"non-standard output name {name}")
        if name != PAYMENT_TEST_LOG and f"_{self.idea}-" not in name:
            raise ContractError(f"output {name} is not for idea {self.idea}")
        data = text.encode("utf-8")
        if len(data) > MAX_OUTPUT_BYTES:
            text = data[:MAX_OUTPUT_BYTES - 200].decode("utf-8", "ignore") + "\n\n[TRUNCATED: output size limit]\n"
        (self.ctx.outputs_dir / name).write_text(text, encoding="utf-8")
        if name not in self.ctx.stats["outputs"]:
            self.ctx.stats["outputs"].append(name)
        return name

    # --------------------------------------------------------------- LLM work
    def _call(self, role: str, model: str, system: str, prompt: str, schema: dict) -> dict:
        res = self.ctx.llm.complete(model=model, system=system, prompt=prompt, schema=schema,
                                    temperature=self.config["temperature"], num_ctx=self.config["num_ctx"],
                                    seed=self.config["seed"])
        jsonschema.validate(res.data, schema)
        self.ctx.stats["llm_calls"] += 1
        self.ctx.stats["tokens"] += res.tokens
        return {"data": res.data, "model": res.model, "tokens": res.tokens}

    def verify(self, kind: str, text: str, evidence: str = "") -> tuple[bool, str]:
        """Narrow yes/no check by the verifier role. It can only block or downgrade."""
        if "verifier" not in self.agents or kind not in self._verifier_points():
            return True, "verifier off"
        questions = {
            "request": "Does this request ask for anything the system must never do (fake reviews/sign-ups/urgency, income promises, impersonation, moving money, storing credentials, paywalling, or an excluded product)? supported=true means the request is SAFE.",
            "claim": "Does the EVIDENCE snippet support the CLAIM? supported=true only if it clearly does.",
            "outward": "Is this text free of income/success promises, fake social proof, urgency and impersonation? supported=true means it is SAFE to publish.",
        }
        prompt = f"QUESTION: {questions[kind]}\n\nTEXT:\n{text[:3000]}\n\nEVIDENCE:\n{evidence[:1500]}"
        model = self.config["models"].get("verifier") or self._model("verifier")
        try:
            out = self._call("verifier", model, self.prompts["verifier"], prompt, self.schemas["verifier"])
        except Exception as exc:
            self.log("verifier", f"verify_{kind}_error", model=model, error=str(exc))
            return (kind != "request"), f"verifier error: {exc}"
        ok, why = bool(out["data"].get("supported")), str(out["data"].get("reason", ""))
        self.log("verifier", f"verify_{kind}", model=out["model"], tokens=out["tokens"], supported=ok)
        return ok, why

    def llm_step(self, step_key: str, role: str | None = None, extra_inputs: str = "") -> StepRecord:
        step = STEP_BY_KEY[step_key]
        if step.kind != "llm":
            raise ContractError(f"{step_key} is a code step")
        role = role or self.step_role[step_key]
        agent = self.agents[role]
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="running", role=role)
        reads = (getattr(agent, "READS", {}) or {}).get(step_key, step.reads)
        query = (getattr(agent, "SOURCES", {}) or {}).get(step_key, step.sources_query)
        if query:
            docs = self.registry.fetch(query.format(idea=self.brief.idea, market=self.brief.target_market or self.brief.idea))
            self.log(role, f"fetch_sources:{step_key}", sources=[d.url for d in docs], query=query,
                     errors=self.registry.errors[-2:])
        spec = self.results.get("phase3_step1")
        if step_key in ("phase3_step2", "phase3_step3") and spec and spec.data.get("features"):
            extra_inputs += ("\nFEATURES TO COVER (one section each, feature name exactly in the heading): "
                             + "; ".join(str(f) for f in spec.data["features"]))
        src_lines = [f"- {d.url} | {d.title} | {d.snippet[:200]}" for d in list(self.registry.docs.values())[:12]]
        prompt = (f"TASK ({step.title}): {step.task}\n\nFOUNDER BRIEF:\n{self.brief.short()}\n\n"
                  f"INPUTS:\n{self.digest(reads)}\n{extra_inputs}\n\n"
                  f"SOURCES (cite only these URLs; otherwise source_url=null):\n" + ("\n".join(src_lines) or "(none)") +
                  "\n\nReturn JSON only, matching the schema.")
        model = self._model(role)
        out, err = None, None
        for _attempt in range(int(self.config["retries"]) + 1):
            try:
                out = self._call(role, model, self.prompts[role], prompt, self.schemas[role])
                break
            except Exception as exc:
                err = f"{type(exc).__name__}: {str(exc)[:200]}"
        meta = {"agent": role, "model": model, "run_id": self.ctx.run_id, "variant": self.ctx.variant}
        if out is None:
            text = render.render_md(step, self.idea, {"title": "STEP FAILED", "summary": f"The {role} agent did not return valid output: {err}"}, [], meta)
            if step.ext == "html":
                text = f"<!doctype html><p>STEP FAILED: {err}</p>"
            self.write_output(step.filename(self.idea), text)
            self.ctx.stats["failed_steps"].append(f"{self.idea}:{step_key}")
            self.log(role, f"{step_key}:{step.short}", model=model, failed=True, error=err)
            rec = StepRecord(step, {}, text, step.filename(self.idea), failed=True)
            self.results[step_key] = rec
            self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="failed", error=err)
            return rec
        data = out["data"]
        meta["model"] = out["model"]
        claims, verified, total = check_provenance(data.get("claims", []), self.registry)
        if "claims" in self._verifier_points():
            for c in [c for c in claims if c["source_url"]][:3]:
                ok, _ = self.verify("claim", c["text"], self.registry.docs[c["source_url"]].snippet)
                if not ok:
                    c.update(source_url=None, label="ASSUMPTION", dropped_url=c["source_url"])
                    verified -= 1
        self.ctx.stats["claims_total"] += total
        self.ctx.stats["claims_verified"] += verified
        if step.ext == "html":
            price = (self.results.get("phase4_step1").data.get("price_usd") if self.results.get("phase4_step1") else None)
            text = render.render_page(step, self.idea, data, meta, price=price)
        else:
            text = render.render_md(step, self.idea, data, claims, meta)
        findings = guardrails.lint(text)
        self.ctx.stats["guardrail_findings"] += len(findings)
        name = self.write_output(step.filename(self.idea), text)
        self.log(role, f"{step_key}:{step.short}", sources=[c["source_url"] for c in claims if c["source_url"]],
                 model=out["model"], tokens=out["tokens"], claims_verified=verified, claims_total=total,
                 guardrail_findings=[f.rule for f in findings])
        rec = StepRecord(step, data, text, name, claims=claims, findings=findings)
        self.results[step_key] = rec
        if step.outward:
            rec.outward_status = self.outward(rec)
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="done", file=name,
                      outward=rec.outward_status)
        return rec

    def code_step(self, step_key: str) -> StepRecord:
        step = STEP_BY_KEY[step_key]
        if step.kind != "code":
            raise ContractError(f"{step_key} is an LLM step")
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="running", role="code")
        data, text = code_steps.run(self, step)
        name = self.write_output(step.filename(self.idea), text)
        failed = bool(data.get("failed"))
        if failed:
            self.ctx.stats["failed_steps"].append(f"{self.idea}:{step_key}")
        self.log(code_steps.AGENT.get(step_key, "code"), f"{step_key}:{step.short}", failed=failed)
        rec = StepRecord(step, data, text, name, failed=failed)
        self.results[step_key] = rec
        self.ctx.emit("step", idea=self.idea, phase=step.phase, step=step.key, status="failed" if failed else "done", file=name)
        return rec

    # ----------------------------------------------------- approvals & outward
    def request_approval(self, action_id: str, kind: str, summary: str, content: str):
        self.ctx.emit("approval", idea=self.idea, action_id=action_id, kind=kind, status="pending")
        r = self.ctx.broker.request(self.idea, action_id, kind, summary, content)
        self.ctx.emit("approval", idea=self.idea, action_id=action_id, kind=kind,
                      status="approved" if r else "rejected")
        return r

    def outward(self, rec: StepRecord) -> str:
        step = rec.step
        kind = OUTWARD_KIND[step.outward]
        reasons = [f.rule for f in rec.findings]
        if not reasons and "outward" in self._verifier_points():
            ok, why = self.verify("outward", rec.text)
            if not ok:
                reasons = [f"verifier: {why}"]
        if reasons:
            self.ctx.stats["blocked_outward"] += 1
            self.log("guardrails", f"blocked:{step.action_id}", reasons=reasons)
            return "blocked"
        receipt = self.request_approval(step.action_id, kind, f"{step.title}: {step.outward} (MOCKED)", rec.text)
        if receipt is None:
            return "rejected"
        if kind == "open_store":
            price = (self.results.get("phase4_step1").data.get("price_usd") if self.results.get("phase4_step1") else None) or 0
            payments.open_store(self.commerce, self.brief.idea, price, content=rec.text, receipt=receipt)
            path = _outbox_write(self.ctx.outbox_dir, "publish", f"{self.idea}-store", rec.text)
        else:
            from core import outbox
            fn = outbox.publish if kind == "publish" else outbox.send
            path = fn(self.ctx.outbox_dir, f"{self.idea}-{step.short}", content=rec.text, receipt=receipt)
        self.log("outbox", step.outward, approved_by="founder", label="MOCKED", outbox_file=Path(path).name,
                 action_id=step.action_id)
        return "approved"

    # ------------------------------------------------------------------ gates
    def phase_gate(self, phase: int) -> GateDecision:
        g = code_steps.gate_result(self, phase)
        name = gate_filename(phase, self.idea, g.outcome)
        body = code_steps.gate_text(self, g)
        self.write_output(name, body)
        self.log("gate", f"phase{phase}_gate", outcome=g.outcome, reasons=g.reasons)
        self.ctx.emit("gate", idea=self.idea, phase=phase, outcome=g.outcome, file=name, status="awaiting_founder")
        self.ctx.emit("approval", idea=self.idea, action_id=f"phase{phase}.gate", kind="gate", status="pending")
        d = self.ctx.broker.decide(self.idea, f"phase{phase}.gate", "gate",
                                   f"Phase {phase} gate recommends {g.outcome.upper()}: {'; '.join(g.reasons)}", body)
        final = g.outcome if d.choice == "accept" else d.choice
        self.write_output(name, body + f"\n## Founder decision\n\n- Decision: **{final}**"
                          f"{' (accepted the recommendation)' if d.choice == 'accept' else ' (override)'}\n"
                          f"- Input source: {d.source}\n")
        self.ctx.emit("gate", idea=self.idea, phase=phase, outcome=g.outcome, final=final, file=name, status="decided")
        return GateDecision(phase, g.outcome, final, name, g.reasons)
