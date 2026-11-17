"""Acceptance tests for a written guide. Runs in the QA sandbox (standard library only)."""
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SPEC = json.loads((HERE / "spec.json").read_text())
FORBIDDEN = re.compile(r"guarantee[sd]?\b.{0,40}(income|revenue|results|money|\$)|\bcountdown\b|\btestimonial|\bonly \d+ left\b|\bget rich\b|\bpassive income\b", re.I)


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().lower()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


class GuideAcceptance(unittest.TestCase):
    def setUp(self):
        self.path = HERE / "guide.md"
        self.assertTrue(self.path.exists(), "guide.md is missing")
        self.text = self.path.read_text()

    def test_has_title(self):
        self.assertTrue(self.text.startswith("# "), "guide must start with a '# ' title")

    def test_required_sections_present(self):
        found = sections(self.text)
        missing = [s["heading"] for s in SPEC["sections"] if s["heading"].strip().lower() not in found]
        self.assertFalse(missing, f"missing sections: {missing}")

    def test_sections_long_enough(self):
        found = sections(self.text)
        minimum = SPEC.get("min_words_per_section", 80)
        short = [h for h, body in found.items() if len(body.split()) < minimum]
        self.assertFalse(short, f"sections under {minimum} words: {short}")

    def test_no_forbidden_claims(self):
        m = FORBIDDEN.search(self.text)
        self.assertIsNone(m, f"forbidden claim: {m.group(0) if m else ''}")


if __name__ == "__main__":
    unittest.main()
