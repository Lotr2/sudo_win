"""One entry point for every variant: load it, run one or more briefs (in parallel), follow founder pivots,
save state after every step, and resume a stopped run from runs/<run>/state.json."""
from __future__ import annotations

import importlib.util
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from approval import ApprovalBroker, load_decisions
from core.base_variant import DONE_STATUSES, BaseVariant, ContractError, RunContext
from core.brief import load_brief
from core.llm import make_client
from core.log import DecisionLog
from core.state import RunState

ROOT = Path(__file__).resolve().parent.parent
VARIANTS_DIR = ROOT / "variants"


def list_variants() -> list[str]:
    return sorted(p.name for p in VARIANTS_DIR.iterdir() if (p / "orchestrator.py").exists()) if VARIANTS_DIR.exists() else []


def load_variant_class(name: str) -> type[BaseVariant]:
    path = VARIANTS_DIR / name / "orchestrator.py"
    if not path.exists():
        raise ContractError(f"variant {name!r} has no orchestrator.py")
    spec = importlib.util.spec_from_file_location(f"variant_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "Variant", None)
    if not (isinstance(cls, type) and issubclass(cls, BaseVariant)):
        raise ContractError(f"variants/{name}/orchestrator.py must define class Variant(BaseVariant)")
    return cls


def _resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() or p.exists() else ROOT / p


def run_variant(variant: str | None, brief_paths=None, decisions_path=None, *, fake: bool = False,
                faults: dict | None = None, out_root: str | Path = ROOT / "runs", demo: bool = False,
                run_dir: str | Path | None = None, queue_dir: str | Path | None = None, interactive: bool = False,
                resume: bool = False, max_ideas: int = 3) -> dict:
    """Run briefs (str or list) through a variant. resume=True continues run_dir from its state.json."""
    if resume:
        run_dir = Path(run_dir)
        state = RunState(run_dir / "state.json")
        m = state.meta
        if not m:
            raise ValueError(f"{run_dir} has no state.json to resume")
        variant, brief_paths, decisions_path = m["variant"], m["briefs"], m.get("decisions")
        fake, demo = m.get("fake", False), m.get("demo", False)
        queue_dir = queue_dir or m.get("queue_dir")
        outputs_dir, log_path, stamp = Path(m["outputs_dir"]), Path(m["log_path"]), m["run_id"]
    else:
        if isinstance(brief_paths, (str, Path)):
            brief_paths = [brief_paths]
        if demo and fake:
            raise ValueError("--demo writes outputs/ and decision_log.json, which must come from a real model run")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = Path(run_dir) if run_dir else Path(out_root) / variant / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        if demo:
            outputs_dir, log_path = ROOT / "outputs", ROOT / "decision_log.json"
            if outputs_dir.exists():
                shutil.rmtree(outputs_dir)
        else:
            outputs_dir, log_path = run_dir / "outputs", run_dir / "decision_log.json"
        state = RunState(run_dir / "state.json")
        state.set_meta(variant=variant, briefs=[str(b) for b in brief_paths],
                       decisions=str(decisions_path) if decisions_path else None, fake=fake, demo=demo,
                       queue_dir=str(queue_dir) if queue_dir else None, outputs_dir=str(outputs_dir),
                       log_path=str(log_path), run_id=stamp, created=time.time())
    outputs_dir.mkdir(parents=True, exist_ok=True)
    log = DecisionLog(log_path, append=resume)
    decisions = load_decisions(decisions_path)
    broker = ApprovalBroker(log, decisions, queue_dir=queue_dir, interactive=interactive, state=state)
    ctx = RunContext(run_id=stamp, variant=variant, run_dir=run_dir, outputs_dir=outputs_dir,
                     outbox_dir=run_dir / "outbox", log=log, llm=make_client(fake, faults), broker=broker,
                     state=state, fake=fake)
    cls = load_variant_class(variant)
    started: set[str] = set()
    lock = threading.Lock()

    def chain(first) -> list[dict]:
        out, path = [], _resolve(first)
        for _ in range(max_ideas):
            brief = load_brief(path)
            with lock:
                if brief.id in started:          # another brief chain already runs this idea
                    break
                started.add(brief.id)
            st = state.idea(brief.id)
            if resume and st.get("status") in DONE_STATUSES and st.get("result"):
                res = st["result"]
            else:
                res = cls(ctx, VARIANTS_DIR / variant).execute(brief, decisions).__dict__
            out.append(res)
            if res["status"] == "pivot" and res.get("next_brief"):
                path = _resolve(res["next_brief"])
                continue
            break
        return out

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, len(brief_paths))) as ex:
        futures = [ex.submit(chain, b) for b in brief_paths]
    results = [r for f in futures for r in f.result()]
    router = getattr(ctx.llm, "router", None)
    summary = {"variant": variant, "run_id": stamp, "fake_llm": fake, "demo": demo, "resumed": resume,
               "run_dir": str(run_dir), "outputs_dir": str(outputs_dir), "log_path": str(log_path),
               "runtime_s": round(time.time() - t0, 1), "results": results, "stats": ctx.stats,
               "endpoints": {e.name: {"served": e.served, "failures": e.failures} for e in router.endpoints} if router else {}}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return summary
