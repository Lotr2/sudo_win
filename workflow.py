"""Entry point: chains the six phases in order.

Usage: python workflow.py [run | test]
"""

from __future__ import annotations

import subprocess
import sys

PHASES = [
    "phase1_discover",
    "phase2_validate",
    "phase3_build",
    "phase4_commercial_setup",
    "phase5_sell_deliver",
    "phase6_operate_learn",
]


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "run"
    if command == "test":
        return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    if command == "run":
        print("Pipeline not implemented yet. Phases:", ", ".join(PHASES))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
