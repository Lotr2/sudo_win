"""CLI entry point (the orchestrator process), identical for every variant:

    ARCH_VARIANT=<name> python -m app.run --brief demo/founder_brief_idea1.md
    python -m app.run --brief a.md --brief b.md          # several ideas at once
    python -m app.run --resume runs/<variant>/<run_id>   # continue a stopped or paused run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runner import run_variant  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one architecture variant on founder briefs.")
    p.add_argument("--brief", action="append", help="founder brief (repeat to run several ideas in parallel)")
    p.add_argument("--resume", metavar="RUN_DIR", help="continue a run from its state.json")
    p.add_argument("--decisions", default=str(ROOT / "demo" / "founder_decisions.yaml"),
                   help="founder-authored decisions file; use '' to decide in the UI or terminal")
    p.add_argument("--variant", default=os.environ.get("ARCH_VARIANT", "_template"))
    p.add_argument("--run-dir", help="where to keep this run (default runs/<variant>/<timestamp>)")
    p.add_argument("--queue-dir", help="approval queue the UI answers (default: none)")
    p.add_argument("--fake-llm", action="store_true", help="fake endpoints (tests/structure checks only)")
    p.add_argument("--demo", action="store_true", help="write the committed outputs/ and decision_log.json (real model only)")
    p.add_argument("--interactive", action="store_true", help="ask the founder on the terminal when no decision exists")
    a = p.parse_args(argv)
    if a.resume:
        s = run_variant(None, resume=True, run_dir=a.resume, queue_dir=a.queue_dir, interactive=a.interactive)
    elif a.brief:
        s = run_variant(a.variant, a.brief, a.decisions or None, fake=a.fake_llm, demo=a.demo, run_dir=a.run_dir,
                        queue_dir=a.queue_dir, interactive=a.interactive)
    else:
        p.error("give --brief (one or more) or --resume RUN_DIR")
    print(json.dumps({k: s[k] for k in ("variant", "run_dir", "runtime_s", "results", "endpoints")}, indent=1, default=str))
    st = s["stats"]
    print(f"llm_calls={st['llm_calls']} tokens={st['tokens']} json_retries={st['json_retries']} "
          f"truncations={st['truncations']} cached_steps={st['cached_steps']} failed_steps={len(st['failed_steps'])} "
          f"outputs={len(st['outputs'])}")
    return 0 if all(r["status"] in ("completed", "stopped", "pivot") for r in s["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
