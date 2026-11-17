"""CLI entry point, identical for every variant:

    ARCH_VARIANT=<name> python -m app.run --brief demo/founder_brief_idea1.md
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
    p = argparse.ArgumentParser(description="Run one architecture variant on a founder brief.")
    p.add_argument("--brief", required=True)
    p.add_argument("--decisions", default=str(ROOT / "demo" / "founder_decisions.yaml"),
                   help="founder-authored decisions file; use '' to decide interactively")
    p.add_argument("--variant", default=os.environ.get("ARCH_VARIANT", "_template"))
    p.add_argument("--fake-llm", action="store_true", help="deterministic fake model (tests/structure checks only)")
    p.add_argument("--demo", action="store_true", help="write the committed outputs/ and decision_log.json (real model only)")
    p.add_argument("--interactive", action="store_true", help="ask the founder on the terminal when no decision exists")
    a = p.parse_args(argv)
    s = run_variant(a.variant, a.brief, a.decisions or None, fake=a.fake_llm, demo=a.demo, interactive=a.interactive)
    print(json.dumps({k: s[k] for k in ("variant", "run_dir", "runtime_s", "results")}, indent=1, default=str))
    st = s["stats"]
    print(f"llm_calls={st['llm_calls']} tokens={st['tokens']} failed_steps={len(st['failed_steps'])} outputs={len(st['outputs'])}")
    return 0 if all(r["status"] in ("completed", "stopped", "pivot") for r in s["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
