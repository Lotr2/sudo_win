"""One entry point for every variant: load it, run the brief (and the founder's pivot brief), collect stats."""
from __future__ import annotations

import importlib.util
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from approval import ApprovalBroker, load_decisions
from core.base_variant import BaseVariant, ContractError, RunContext
from core.brief import load_brief
from core.llm import make_client
from core.log import DecisionLog

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


def run_variant(variant: str, brief_path: str | Path, decisions_path: str | Path | None = None, *, fake: bool = False,
                out_root: str | Path = ROOT / "runs", demo: bool = False, run_dir: str | Path | None = None,
                queue_dir: str | Path | None = None, interactive: bool = False, max_ideas: int = 3) -> dict:
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
    outputs_dir.mkdir(parents=True, exist_ok=True)
    log = DecisionLog(log_path)
    decisions = load_decisions(decisions_path)
    broker = ApprovalBroker(log, decisions, queue_dir=queue_dir, interactive=interactive)
    ctx = RunContext(run_id=stamp, variant=variant, run_dir=run_dir, outputs_dir=outputs_dir,
                     outbox_dir=run_dir / "outbox", log=log, llm=make_client(fake), broker=broker, fake=fake)
    cls = load_variant_class(variant)
    t0 = time.time()
    results, path = [], Path(brief_path)
    for _ in range(max_ideas):
        brief = load_brief(path)
        v = cls(ctx, VARIANTS_DIR / variant)
        res = v.execute(brief, decisions)
        results.append(res.__dict__)
        if res.status == "pivot" and res.next_brief:
            path = Path(res.next_brief) if Path(res.next_brief).is_absolute() else ROOT / res.next_brief
            continue
        break
    summary = {"variant": variant, "run_id": stamp, "fake_llm": fake, "demo": demo, "run_dir": str(run_dir),
               "outputs_dir": str(outputs_dir), "log_path": str(log_path), "runtime_s": round(time.time() - t0, 1),
               "results": results, "stats": ctx.stats}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return summary
