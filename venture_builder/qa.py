"""QA Runner [code]: runs a product's acceptance tests in a sandbox with no network.

The tests run in a separate Python process started in isolated mode (`-I`, empty environment, product
directory as the working directory). An audit hook installed before the tests start blocks every socket,
DNS lookup and subprocess call. When the host allows unprivileged user namespaces, the process also runs
in its own network namespace (`unshare -rn`), so it has no network interface at all.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .decision_log import log_action

BOOTSTRAP = r"""
import sys, unittest
BLOCKED = ("socket.", "subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
           "urllib.Request", "ctypes.")
def _hook(event, args):
    if event.startswith(BLOCKED):
        raise PermissionError(f"sandbox: {event} is blocked")
sys.addaudithook(_hook)
suite = unittest.defaultTestLoader.discover(".", pattern="test_acceptance.py")
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
print("__RESULT__", result.testsRun, len(result.failures) + len(result.errors))
"""


@functools.lru_cache(maxsize=1)
def _netns_available() -> bool:
    if not shutil.which("unshare"):
        return False
    try:
        r = subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def run_acceptance(product_dir: Path, timeout: int = 60) -> dict:
    cmd = [sys.executable, "-I", "-c", BOOTSTRAP]
    isolation = "audit hook (sockets, DNS and subprocesses blocked)"
    if _netns_available():
        cmd = ["unshare", "-rn", *cmd]
        isolation = "network namespace with no interfaces + audit hook"
    try:
        proc = subprocess.run(cmd, cwd=product_dir, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        out = "TIMEOUT"
    m = re.search(r"__RESULT__ (\d+) (\d+)", out)
    total, failed = (int(m.group(1)), int(m.group(2))) if m else (0, 1)
    failures = re.findall(r"^(?:FAIL|ERROR): (\S+).*?\n-+\n(.*?)(?=\n={10,}|\n-{10,}\nRan|\Z)", out, re.M | re.S)
    report = {
        "total": total,
        "passed": total - failed,
        "failed": failed,
        "isolation": isolation,
        "failures": [f"{name}: {detail.strip().splitlines()[-1] if detail.strip() else ''}" for name, detail in failures][:20],
        "output": out[-4000:],
    }
    log_action("qa_runner", "run_acceptance_tests", passed=report["passed"], total=total, isolation=isolation)
    return report


def write_report_json(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=1))
