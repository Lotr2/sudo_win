"""Acceptance tests for a template pack. Runs in the QA sandbox (standard library only)."""
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SPEC = json.loads((HERE / "spec.json").read_text())
FORBIDDEN = re.compile(r"guarantee[sd]?\b.{0,40}(income|revenue|results|money|\$)|\bcountdown\b|\btestimonial|\bget rich\b", re.I)


class PackAcceptance(unittest.TestCase):
    def setUp(self):
        self.items = sorted((HERE / "templates").glob("*.md")) if (HERE / "templates").exists() else []

    def test_item_count(self):
        self.assertEqual(len(self.items), SPEC["item_count"], "wrong number of templates")

    def test_every_item_has_required_fields(self):
        for path in self.items:
            text = path.read_text().lower()
            missing = [f for f in SPEC.get("item_fields", []) if f"## {f.strip().lower()}" not in text]
            self.assertFalse(missing, f"{path.name} missing fields {missing}")

    def test_items_not_empty(self):
        for path in self.items:
            self.assertGreater(len(path.read_text().split()), 40, f"{path.name} is too short")

    def test_index_lists_all_items(self):
        index = (HERE / "index.md").read_text()
        for path in self.items:
            self.assertIn(f"templates/{path.name}", index)

    def test_no_forbidden_claims(self):
        for path in self.items:
            self.assertIsNone(FORBIDDEN.search(path.read_text()), path.name)


if __name__ == "__main__":
    unittest.main()
