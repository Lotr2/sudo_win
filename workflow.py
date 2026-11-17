"""One entry point for the whole six-phase pipeline.

  python workflow.py                 where you are, and the next command to run
  python workflow.py <phase> [args]  run a phase's commands (args are passed through)
  python workflow.py test            run every phase's smoke tests
  python workflow.py up              container start-up: show status, then stay running for `exec`

Phases: discover, validate, build, commercial, launch, operate
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"

PHASES = {
    "discover": ("phase-1-discover", "Phase 1: Discover"),
    "validate": ("phase-2-validate", "Phase 2: Validate"),
    "build": ("phase-3-build", "Phase 3: Build"),
    "commercial": ("phase-4-commercial-setup", "Phase 4: Commercial setup"),
    "launch": ("phase-5-launch", "Phase 5: Launch"),
    "operate": ("phase-6-operate-and-learn", "Phase 6: Operate and learn"),
}

# (file that proves the step is done, what to run next if it isn't)
STEPS = [
    ("phase1_step3_handoff.json", "discover", "python workflow.py discover            # answer questions until the idea is clear"),
    ("phase2_step2_demand_test_plan.json", "validate", "python workflow.py validate plan       # Lean Canvas, demand test, landing page"),
    ("phase2_step4_handoff.json", "validate", "python workflow.py validate serve      # collect sign-ups, then: python workflow.py validate gate"),
    ("phase3_step5_handoff.json", "build", "python workflow.py build build         # tests first, then code; try it with: build serve"),
    ("phase4_step4_handoff.json", "commercial", "python workflow.py commercial          # price and policies"),
    ("phase5_step5_handoff.json", "launch", "python workflow.py launch prepare      # then: launch test-run, launch gate"),
    ("phase6_step1_targets.json", "operate", "python workflow.py operate targets      # lock review targets before results"),
    ("phase6_gate_review.md", "operate", "python workflow.py launch serve        # take orders; support: operate support; then: operate review"),
]


def status() -> str:
    lines = ["Open-source agentic venture builder: six phases, each ending in a gate.", ""]
    nxt = None
    for done_file, phase, command in STEPS:
        done = (OUT / done_file).exists()
        lines.append(f"  [{'x' if done else ' '}] {PHASES[phase][1]}: {done_file}")
        if not done and nxt is None:
            nxt = command
    stop = [f for f in ("phase1_gate_decision.md", "phase2_gate_demand_test.md", "phase6_gate_review.md")
            if (OUT / f).exists() and "**Outcome:** STOP" in (OUT / f).read_text()]
    lines.append("")
    if stop:
        lines.append(f"  A gate recommended STOP ({', '.join(stop)}). Read it before going on.")
    if (OUT / "phase6_step5_next_brief.json").exists():
        lines.append("  Pivot brief ready: python workflow.py discover --brief outputs/phase6_step5_next_brief.json")
    lines.append(f"  Next: {nxt}" if nxt else "  All phases have run. Review outputs/ and decision_log.json.")
    return "\n".join(lines)


def run_phase(name: str, args: list[str]) -> int:
    folder, _ = PHASES[name]
    return subprocess.call([sys.executable, str(ROOT / folder / "orchestrator.py"), *args], cwd=ROOT)


def run_tests() -> int:
    return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT)


def main(argv: list[str]) -> int:
    if not argv:
        print(status())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in PHASES:
        return run_phase(cmd, rest)
    if cmd == "test":
        return run_tests()
    if cmd == "up":
        print(status(), flush=True)
        print("\nReady. Open the app in another terminal:\n  docker compose exec app python workflow.py\n", flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        signal.pause()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
