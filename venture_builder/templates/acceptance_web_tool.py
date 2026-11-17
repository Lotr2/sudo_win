"""Acceptance tests for a small single-page web tool. Runs in the QA sandbox (standard library only).
Checks structure and safety statically; behaviour in a browser is not executed here."""
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).parent
SPEC = json.loads((HERE / "spec.json").read_text())


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.external = set(), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        for key in ("src", "href", "action"):
            if a.get(key, "").startswith(("http:", "https:", "//")):
                self.external.append(a[key])


class WebToolAcceptance(unittest.TestCase):
    def setUp(self):
        self.html = (HERE / "index.html").read_text()
        self.p = Ids()
        self.p.feed(self.html)

    def test_title(self):
        self.assertRegex(self.html, r"<title>[^<]{3,}</title>")

    def test_inputs_present(self):
        missing = [i["id"] for i in SPEC["inputs"] if i["id"] not in self.p.ids]
        self.assertFalse(missing, f"missing inputs {missing}")

    def test_result_and_button(self):
        self.assertIn("result", self.p.ids)
        self.assertIn("calculate", self.p.ids)

    def test_compute_returns_value(self):
        body = re.search(r"function compute\(v\) \{(.*?)\n    \}\n", self.html, re.S)
        self.assertIsNotNone(body, "compute function missing")
        self.assertIn("return", body.group(1))

    def test_no_network_or_eval(self):
        self.assertFalse(self.p.external, f"external resources: {self.p.external}")
        bad = re.search(r"\b(fetch|XMLHttpRequest|WebSocket|eval|import\(|navigator\.sendBeacon|localStorage)\b", self.html)
        self.assertIsNone(bad, f"forbidden API: {bad.group(0) if bad else ''}")


if __name__ == "__main__":
    unittest.main()
