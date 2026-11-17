"""Repository smoke tests.

1. Every phase's own smoke tests pass (pipeline steps, gates, founder approval). Each phase runs in its own process
   because phases share module names such as `agents` and `gates`.
2. The repository follows the submission conventions: README headings, licence, one-command setup, architecture
   sections, prompts, and the decision-log and outputs formats once a demo run exists.

Run: python -m unittest discover -s tests   (or: python workflow.py test)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHASES = sorted(p.name for p in ROOT.glob("phase-*") if (p / "tests").is_dir())

README_HEADINGS = ["## Quickstart", "## What works and what is mocked", "## Architecture",
                   "## Models and free resources", "## Cost per run", "## Demo video",
                   "## What our system will NOT do", "## How the project stays alive after the hackathon"]
ARCH_HEADINGS = ["## Overview", "## Phases", "## Gates", "## Sale to cash", "## Data flow", "## Built vs. stubbed",
                 "## Roadmap"]
LOG_KEYS = ("action", "sources", "model", "cost", "approved_by")


def headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith("## ")]


class PhaseSuites(unittest.TestCase):
    def test_all_six_phases_present(self):
        self.assertEqual(len(PHASES), 6, PHASES)

    def test_each_phase_suite_passes(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", f"{phase}/tests"],
                                      cwd=ROOT, capture_output=True, text=True, timeout=600)
                self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])


class Conventions(unittest.TestCase):
    def test_readme_headings_in_order(self):
        text = (ROOT / "README.md").read_text()
        self.assertEqual(headings(text), README_HEADINGS)
        self.assertIn("```bash\ndocker compose up\n```", text)
        cost = text.split("## Cost per run")[1].split("## ")[0]
        self.assertIn("$", cost)
        table = text.split("## What works and what is mocked")[1].split("## ")[0]
        for status in ("Working", "MOCKED", "STUBBED"):
            self.assertIn(status, table)

    def test_licence_is_full_osi_text(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND', text)

    def test_one_command_setup(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        for service in ("ollama:", "model:", "app:"):
            self.assertIn(service, compose)
        self.assertTrue((ROOT / "Dockerfile").exists())

    def test_architecture_sections_and_text_diagrams(self):
        text = (ROOT / "ARCHITECTURE.md").read_text()
        self.assertEqual(headings(text), ARCH_HEADINGS)
        self.assertIn("```mermaid", text)
        for phase in ("Discover", "Validate", "Build", "Commercial setup", "Launch", "Operate and learn"):
            self.assertIn(f"### Phase", text)
            self.assertIn(phase, text)
        for idea in ("Payment triggers delivery", "refund revokes access", "Idempotency", "Reconciliation", "STOP"):
            self.assertIn(idea.lower(), text.lower())

    def test_every_prompt_an_agent_loads_exists(self):
        names = set()
        for f in ROOT.glob("phase-*/agents/*.py"):
            names |= set(re.findall(r'load_prompt\("(\w+)"\)', f.read_text()))
            names |= set(re.findall(r'prompt = "(\w+)"', f.read_text()))
            names |= {m for m in re.findall(r'name, prompt, [^=]*= "\w+", "(\w+)"', f.read_text())}
        self.assertTrue(names)
        for n in sorted(names):
            self.assertTrue((ROOT / "prompts" / f"{n}.md").exists(), n)

    def test_env_example_has_placeholders_only(self):
        text = (ROOT / ".env.example").read_text()
        self.assertNotRegex(text, r"(sk-|ghp_|AKIA)[A-Za-z0-9]{10,}")
        self.assertIn("LLM_BASE_URL", text)
        ignore = (ROOT / ".gitignore").read_text()
        for entry in (".env", "runtime/"):
            self.assertIn(entry, ignore)

    def test_decision_log_format(self):
        path = ROOT / "decision_log.json"
        if not path.exists():
            self.skipTest("no demo run committed yet")
        entries = json.loads(path.read_text())
        self.assertIsInstance(entries, list)
        for e in entries:
            for key in LOG_KEYS:
                self.assertIn(key, e)
            self.assertIsInstance(e["sources"], list)
            self.assertIsInstance(e["cost"], (int, float))
            self.assertIn(e["approved_by"], (None, "founder"))

    def test_outputs_naming(self):
        out = ROOT / "outputs"
        if not out.is_dir():
            self.skipTest("no demo run committed yet")
        pattern = re.compile(r"^(phase\d_(step\d+|gate)_[a-z0-9_]+|payment_test_log)\.(md|json|csv|txt|html)$")
        for f in out.iterdir():
            self.assertRegex(f.name, pattern)
