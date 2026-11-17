"""One entry point for the whole six-phase pipeline.

  python workflow.py                 where you are, and the next command to run
  python workflow.py run             run the pipeline from where you are, one step after another
  python workflow.py <phase> [args]  run a phase's commands (args are passed through)
  python workflow.py reset           clear the previous run (archive it to runs/ or delete it) to start over
  python workflow.py test            run every phase's smoke tests
  python workflow.py up              container start-up: show status, then stay running for `exec`

Phases: discover, validate, build, commercial, launch, operate

Settings come from the environment, then from `.env` at the repo root (see `.env.example`).
`run` asks before every step and stops as soon as a step does not move the pipeline forward
(a gate said repeat, pivot or stop, or you stopped). Nothing in it approves anything for you.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
LOG = ROOT / "decision_log.json"
ARCHIVE = ROOT / "runs"  # git-ignored
MIN_NODE_MAJOR = 25  # Phase 3's sandbox needs Node's permission model to block network access

PHASES = {
    "discover": ("phase-1-discover", "Phase 1: Discover"),
    "validate": ("phase-2-validate", "Phase 2: Validate"),
    "build": ("phase-3-build", "Phase 3: Build"),
    "commercial": ("phase-4-commercial-setup", "Phase 4: Commercial setup"),
    "launch": ("phase-5-launch", "Phase 5: Launch"),
    "operate": ("phase-6-operate-and-learn", "Phase 6: Operate and learn"),
}


@dataclass(frozen=True)
class Step:
    done_file: str          # file in outputs/ that proves the step is done
    phase: str
    args: tuple[str, ...]   # passed to the phase's orchestrator
    what: str               # shown to the founder
    serve_first: tuple[str, ...] = ()  # optional server to run (until Ctrl+C) before the step, to collect evidence
    serve_why: str = ""

    @property
    def command(self) -> str:
        return " ".join(["python workflow.py", self.phase, *self.args])


STEPS = [
    Step("phase1_step3_handoff.json", "discover", (), "answer questions until the idea is clear, then the go/pivot/stop gate"),
    Step("phase2_step2_demand_test_plan.json", "validate", ("plan",), "Lean Canvas, locked demand test, landing page"),
    Step("phase2_step4_handoff.json", "validate", ("gate",), "judge the sign-ups against the locked thresholds",
         serve_first=("validate", "serve"), serve_why="collect waitlist sign-ups at http://127.0.0.1:8000"),
    Step("phase3_step5_handoff.json", "build", ("build",), "spec, tests first, code in the Node sandbox, build gate"),
    Step("phase4_step4_handoff.json", "commercial", (), "pricing with break-even, policies, commercial gate"),
    Step("phase5_step2_launch_posts.md", "launch", ("prepare",), "store page and launch posts, each approved by you"),
    Step("payment_test_log.md", "launch", ("test-run",), "payment and delivery test run (MOCKED payments)"),
    Step("phase5_step5_handoff.json", "launch", ("gate",), "launch gate"),
    Step("phase6_step1_targets.json", "operate", ("targets",), "lock the review targets before any results"),
    Step("phase6_gate_review.md", "operate", ("review",), "metrics, memo, persevere/pivot/stop gate",
         serve_first=("launch", "serve"), serve_why="take (MOCKED) orders at http://127.0.0.1:8002"),
]


# ---------- settings ----------

def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into os.environ. Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def node_major() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)", out.strip())
    return int(m.group(1)) if m else None


# ---------- status ----------

def is_done(step: Step) -> bool:
    path = OUT / step.done_file
    if not path.exists():
        return False
    if step.done_file == "phase2_step2_demand_test_plan.json":
        # the plan is written before the landing page; the step is done only once the page is published
        try:
            return bool(json.loads(path.read_text()).get("started_at"))
        except (OSError, ValueError):
            return False
    return True


def next_step() -> Step | None:
    return next((s for s in STEPS if not is_done(s)), None)


def stopped_gates() -> list[str]:
    return [f for f in ("phase1_gate_decision.md", "phase2_gate_demand_test.md", "phase6_gate_review.md")
            if (OUT / f).exists() and "**Outcome:** STOP" in (OUT / f).read_text()]


def status() -> str:
    lines = ["Open-source agentic venture builder: six phases, each ending in a gate.", ""]
    for s in STEPS:
        lines.append(f"  [{'x' if is_done(s) else ' '}] {PHASES[s.phase][1]}: {s.done_file}")
    lines.append("")
    stop = stopped_gates()
    if stop:
        lines.append(f"  A gate recommended STOP ({', '.join(stop)}). Read it before going on.")
    if (OUT / "phase6_step5_next_brief.json").exists():
        lines.append("  Pivot brief ready: python workflow.py discover --brief outputs/phase6_step5_next_brief.json")
    nxt = next_step()
    if nxt:
        first = " ".join(["python workflow.py", *nxt.serve_first]) + f"   # {nxt.serve_why}, then: " if nxt.serve_first else ""
        lines.append(f"  Next: {first}{nxt.command}")
        lines.append("  Or run every remaining step in order: python workflow.py run")
    else:
        lines.append("  All phases have run. Review outputs/ and decision_log.json.")
    return "\n".join(lines)


# ---------- running ----------

def run_phase(name: str, args: list[str] | tuple[str, ...]) -> int:
    """Run a phase in the foreground. Ctrl+C goes to the phase (it stops cleanly); this process waits for it."""
    folder, _ = PHASES[name]
    if name == "build" and (major := node_major()) is not None and major < MIN_NODE_MAJOR:
        print(f"Phase 3 needs Node.js {MIN_NODE_MAJOR}+ (found v{major}). Its sandbox relies on Node blocking "
              "network access by default.", file=sys.stderr)
        return 2
    proc = subprocess.Popen([sys.executable, str(ROOT / folder / "orchestrator.py"), *args], cwd=ROOT)
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            continue  # the child got the same Ctrl+C and is shutting down


def ask_choice(question: str, options: dict[str, str], input_fn=input) -> str:
    print(question)
    for key, label in options.items():
        print(f"  [{key}] {label}")
    while True:
        try:
            answer = input_fn("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "s"
        if answer in {"/quit", "/exit"}:
            return "s"
        if answer in options:
            return answer
        print(f"Please type one of: {', '.join(options)}")


def run_all(input_fn=input, runner=run_phase) -> int:
    """Run the remaining steps in order, asking before each one. Stops when a step does not complete."""
    while True:
        step = next_step()
        if step is None:
            print("\n" + status())
            return 0
        if stopped_gates() and step.phase != "discover":
            print("\n" + status())
            return 0
        title = PHASES[step.phase][1]
        print(f"\n=== Next: {title}: {step.what} ===")
        pick = ask_choice(f"Run `{step.command}` now?", {"y": "Yes, run it", "s": "Stop here (pick up later with `python workflow.py run`)"}, input_fn)
        if pick == "s":
            print("\n" + status())
            return 0
        if step.serve_first:
            serve = ask_choice(f"First {step.serve_why}? The server runs until you press Ctrl+C.",
                               {"y": "Yes, start it", "n": "No, go straight to the step"}, input_fn)
            if serve == "y":
                runner(step.serve_first[0], step.serve_first[1:])
        code = runner(step.phase, step.args)
        if not is_done(step):
            print(f"\n`{step.command}` ended without moving the pipeline forward (a gate said repeat, pivot or stop, "
                  f"or you stopped; exit code {code}). Read the output above, then pick up with `python workflow.py run`.")
            print(status())
            return code or 1


def runtime_dir() -> Path:
    path = Path(os.environ.get("RUNTIME_DIR") or "runtime")
    return path if path.is_absolute() else ROOT / path


def reset(input_fn=input) -> int:
    """Clear the previous run so the next `run` starts at Phase 1. Asks first; archiving keeps everything."""
    targets = [p for p in (OUT, runtime_dir(), LOG) if p.exists()]
    if not targets:
        print("Nothing to clear: no outputs/, runtime/ or decision_log.json. `python workflow.py run` starts at Phase 1.")
        return 0
    print("This clears the previous run:")
    for p in targets:
        count = sum(1 for f in p.rglob("*") if f.is_file()) if p.is_dir() else 1
        print(f"  {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}  ({count} file{'s' if count != 1 else ''})")
    print("runtime/ holds the waitlist sign-ups, the built product, the store database and support tickets.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pick = ask_choice("\nWhat should happen to it?", {
        "a": f"Archive it to runs/{stamp}/ (git-ignored, nothing lost)",
        "d": "Delete it permanently",
        "c": "Cancel",
    }, input_fn)
    if pick in {"c", "s"}:
        print("Cancelled. Nothing was changed.")
        return 0
    if pick == "a":
        dest = ARCHIVE / stamp
        dest.mkdir(parents=True)
        for p in targets:
            shutil.move(str(p), str(dest / p.name))
        print(f"Archived to {dest.relative_to(ROOT)}/.")
    else:
        for p in targets:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        print("Deleted.")
    print("Start the new run with: python workflow.py run")
    return 0


def run_tests() -> int:
    return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT)


def main(argv: list[str]) -> int:
    load_env()
    if not argv:
        print(status())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in PHASES:
        return run_phase(cmd, rest)
    if cmd == "run":
        return run_all()
    if cmd == "reset":
        return reset()
    if cmd == "test":
        return run_tests()
    if cmd == "up":
        print(status(), flush=True)
        print("\nReady. Open the app in another terminal:\n  docker compose exec app python workflow.py run\n", flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        signal.pause()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
