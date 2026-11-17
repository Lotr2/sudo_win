"""Smoke tests for the repository layout the submission requires."""

import importlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

README_HEADINGS = [
    "Quickstart",
    "What works and what is mocked",
    "Architecture",
    "Models and free resources",
    "Cost per run",
    "Demo video",
    "What our system will NOT do",
    "How the project stays alive after the hackathon",
]

ARCHITECTURE_HEADINGS = [
    "Overview",
    "Phases",
    "Gates",
    "Sale to cash",
    "Data flow",
    "Built vs. stubbed",
    "Roadmap",
]

LOG_KEYS = {"timestamp", "agent", "action", "sources", "model", "cost", "approved_by"}

OUTPUT_NAME = re.compile(r"^(phase\d+_(step\d+|gate)_[\w-]+\.(md|json|csv|txt|html)|payment_test_log\.md|\.gitkeep)$")


def h2(path: Path) -> list[str]:
    return [line[3:].strip() for line in path.read_text().splitlines() if line.startswith("## ")]


class LayoutTest(unittest.TestCase):
    def test_required_root_files(self):
        for name in ["README.md", "LICENSE", "docker-compose.yml", "decision_log.json", "ARCHITECTURE.md", ".env.example"]:
            self.assertTrue((ROOT / name).is_file(), name)
        for name in ["outputs", "tests", "prompts", "docs"]:
            self.assertTrue((ROOT / name).is_dir(), name)

    def test_readme_headings_in_order(self):
        self.assertEqual(h2(ROOT / "README.md"), README_HEADINGS)
        self.assertIn("```bash\ndocker compose up\n```", (ROOT / "README.md").read_text())

    def test_architecture_headings_in_order(self):
        self.assertEqual(h2(ROOT / "ARCHITECTURE.md"), ARCHITECTURE_HEADINGS)

    def test_decision_log_entries(self):
        log = json.loads((ROOT / "decision_log.json").read_text())
        self.assertIsInstance(log, list)
        for entry in log:
            self.assertTrue(LOG_KEYS <= entry.keys(), entry)

    def test_output_file_names(self):
        for path in (ROOT / "outputs").iterdir():
            self.assertRegex(path.name, OUTPUT_NAME)

    def test_phase_packages_import(self):
        import workflow

        for phase in workflow.PHASES:
            importlib.import_module(f"phases.{phase}.orchestrator")
            importlib.import_module(f"phases.{phase}.gates")


if __name__ == "__main__":
    unittest.main()
