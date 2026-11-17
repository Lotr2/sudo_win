"""Checks and runs model-written code safely.

Two layers:
  1. Static scan: rejects network calls, dynamic code, Node/process access, external resources, credential or
     payment fields, and unexpected imports, before anything runs.
  2. Node's permission model: tests run with file reads limited to the build folder, and no writes, network,
     child processes, workers or addons. Plus a hard timeout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

TEST_TIMEOUT = 30

FORBIDDEN = {
    "network access": r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b",
    "dynamic code": r"\beval\s*\(|\bnew\s+Function\b|\bFunction\s*\(|\bimport\s*\(",
    "Node or process access": r"\brequire\s*\(|\bprocess\.|\b__dirname\b|\bchild_process\b",
    "external URL": r"(?:https?:)?//[a-z0-9-]+\.[a-z]",
    "password or payment field": r"type\s*=\s*[\"']password|card[\s_-]?number|\bcvv\b|\bcvc\b|\bstripe\b|\bpaypal\b",
}
# Allowed absolute URLs (XML namespaces are identifiers, not network requests)
URL_ALLOWLIST = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xhtml", "http://www.w3.org/1999/xlink")
TEST_IMPORTS = {"node:test", "node:assert", "node:assert/strict", "./logic.js"}
IMPORT_RE = re.compile(r"^\s*import\b[^'\"]*?from\s*['\"]([^'\"]+)['\"]|^\s*import\s*['\"]([^'\"]+)['\"]", re.M)
EXPORT_RE = re.compile(r"export\s+(?:async\s+)?function\s*\*?\s*(\w+)|export\s+(?:const|let|var|class)\s+(\w+)|export\s*\{([^}]*)\}")
TEST_TITLE_RE = re.compile(r"\b(?:test|it)\s*\(\s*(['\"`])(.*?)\1", re.S)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"(^|[^:\\])//[^\n]*", r"\1", code)


def forbidden(code: str) -> list[str]:
    text = code
    for url in URL_ALLOWLIST:
        text = text.replace(url, "")
    return [name for name, pat in FORBIDDEN.items() if re.search(pat, text, re.IGNORECASE)]


def imports(code: str) -> list[str]:
    return [a or b for a, b in IMPORT_RE.findall(code)]


def exported_names(code: str) -> set[str]:
    names = set()
    for fn, var, group in EXPORT_RE.findall(code):
        names.update(n for n in (fn, var) if n)
        for part in group.split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    return names


def scan_logic(code: str, api_names: list[str]) -> list[str]:
    issues = [f"forbidden: {f}" for f in forbidden(_strip_comments(code))]
    if imports(code):
        issues.append("logic.js must not import anything")
    for word in ("localStorage", "document.", "window.", "Date.now", "Math.random", "setTimeout", "setInterval"):
        if word in _strip_comments(code):
            issues.append(f"logic.js must be pure: remove {word}")
    missing = [n for n in api_names if n not in exported_names(code)]
    if missing:
        issues.append(f"missing exports: {', '.join(missing)}")
    return issues


def test_titles(code: str) -> list[str]:
    return [t for _, t in TEST_TITLE_RE.findall(code)]


def scan_tests(code: str, ac_ids: list[str]) -> list[str]:
    issues = [f"forbidden: {f}" for f in forbidden(_strip_comments(code))]
    bad = [m for m in imports(code) if m not in TEST_IMPORTS]
    if bad:
        issues.append(f"tests may only import node:test, node:assert and ./logic.js (found: {', '.join(bad)})")
    if "./logic.js" not in imports(code):
        issues.append("tests must import from './logic.js'")
    covered = {m for t in test_titles(code) for m in re.findall(r"\bAC\d+\b", t)}
    missing = [a for a in ac_ids if a not in covered]
    if missing:
        issues.append(f"no test title starts with: {', '.join(missing)}")
    for word in ("Date.now", "Math.random", "setTimeout", "setInterval", ".skip(", ".todo(", "skip:", "todo:"):
        if word in code:
            issues.append(f"tests must be deterministic and complete: remove {word}")
    return issues


def module_scripts(html: str) -> list[str]:
    return re.findall(r"<script\b[^>]*type\s*=\s*['\"]module['\"][^>]*>(.*?)</script>", html, re.S | re.I)


def scan_ui(html: str, api_names: list[str]) -> list[str]:
    without_js_comments = re.sub(r"(<script\b[^>]*>)(.*?)(</script>)",
                                 lambda m: m.group(1) + _strip_comments(m.group(2)) + m.group(3), html, flags=re.S | re.I)
    issues = [f"forbidden: {f}" for f in forbidden(without_js_comments)]
    scripts = module_scripts(html)
    if len(scripts) != 1:
        issues.append("index.html must have exactly one <script type=\"module\">")
    other = re.findall(r"<script\b(?![^>]*type\s*=\s*['\"]module['\"])[^>]*>", html, re.I)
    if other:
        issues.append("only one module script is allowed (no other <script> tags)")
    if re.search(r"<(link|img|iframe|object|embed)\b[^>]*\b(src|href)\s*=", html, re.I):
        issues.append("no external files, images or frames")
    if re.search(r"<form\b[^>]*\baction\s*=", html, re.I):
        issues.append("forms must not submit anywhere (no action attribute)")
    if scripts:
        mods = imports(scripts[0])
        if mods != ["./logic.js"]:
            issues.append("the module script must import only from './logic.js'")
        used = {n for n in api_names if re.search(rf"\b{re.escape(n)}\b", scripts[0])}
        if not used:
            issues.append("the page does not call any logic.js function")
    return issues


@dataclass
class TestRun:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failures: str = ""
    skipped: int = 0
    todo: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed and not self.error and bool(self.passed) and not self.skipped and not self.todo


MIN_NODE_MAJOR = 25  # older Node versions let sandboxed code reach the network even with --permission
_checked_nodes: set[str] = set()


def node_path() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError(f"Node.js ({MIN_NODE_MAJOR} or newer) is needed to run the product's tests.")
    if node not in _checked_nodes:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        m = re.match(r"v(\d+)", out)
        if not m or int(m.group(1)) < MIN_NODE_MAJOR:
            raise RuntimeError(f"Node.js {MIN_NODE_MAJOR} or newer is needed (found {out or 'unknown'}): older "
                               "versions do not block network access in the sandbox.")
        _checked_nodes.add(node)
    return node


def run_tests(build_dir: Path, timeout: int = TEST_TIMEOUT) -> TestRun:
    build_dir = Path(build_dir).resolve()
    cmd = [node_path(), "--permission", f"--allow-fs-read={build_dir}", "--test", "--test-isolation=none",
           "--test-reporter=tap", "logic.test.js"]
    try:
        proc = subprocess.run(cmd, cwd=build_dir, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""})
    except subprocess.TimeoutExpired:
        return TestRun(error=f"tests did not finish within {timeout}s (infinite loop?)")
    return parse_tap(proc.stdout, proc.stderr)


def parse_tap(stdout: str, stderr: str = "") -> TestRun:
    run = TestRun()
    lines = stdout.splitlines()
    details: list[str] = []
    for i, line in enumerate(lines):
        m = re.match(r"^(not ok|ok) \d+ - (.*?)(\s+#\s*(SKIP|TODO).*)?$", line)
        if not m:
            continue
        name = m.group(2).strip()
        if m.group(4):
            if m.group(4).upper() == "SKIP":
                run.skipped += 1
            else:
                run.todo += 1
            continue
        if m.group(1) == "ok":
            run.passed.append(name)
        else:
            run.failed.append(name)
            block = [name]
            for nxt in lines[i + 1 : i + 40]:
                if re.match(r"^(not ok|ok) \d+", nxt):
                    break
                if re.search(r"(error|expected|actual|operator|message|code):", nxt) or nxt.strip().startswith(("+", "-")):
                    block.append(nxt.rstrip())
            details.append("\n".join(block[:25]))
    run.failures = "\n\n".join(details)[:4000]
    if not run.passed and not run.failed:
        run.error = (stderr.strip() or stdout.strip() or "no test results")[:2000]
    return run


def check_module_syntax(js: str) -> str:
    """Syntax-check a module script with `node --check`. Returns an error string, or '' if valid."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "page.mjs"
        path.write_text(js)
        proc = subprocess.run([node_path(), "--check", str(path)], capture_output=True, text=True, timeout=15)
        return proc.stderr.strip()[:1500] if proc.returncode else ""
