"""Smoke tests for `workflow.py run`: steps run in order, every step needs a founder yes, and the run
stops as soon as a step does not complete (for example a gate that says stop)."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import workflow  # noqa: E402


class RunAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(workflow, "OUT", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def fake_runner(self, completes=()):
        def runner(phase, args):
            self.calls.append((phase, *args))
            step = workflow.next_step()
            if step and (phase, *args) == (step.phase, *step.args) and step.done_file in completes:
                body = json.dumps({"started_at": "now"}) if step.done_file.endswith("plan.json") else "{}"
                (self.tmp / step.done_file).write_text(body)
            return 0
        return runner

    def run_all(self, answers, completes=()):
        it = iter(answers)
        with redirect_stdout(io.StringIO()):
            return workflow.run_all(input_fn=lambda _: next(it), runner=self.fake_runner(completes))

    def test_runs_steps_in_order_until_founder_stops(self):
        done = {"phase1_step3_handoff.json", "phase2_step2_demand_test_plan.json"}
        self.run_all(["y", "y", "s"], completes=done)
        self.assertEqual(self.calls, [("discover",), ("validate", "plan")])

    def test_nothing_runs_without_a_yes(self):
        self.run_all(["s"])
        self.assertEqual(self.calls, [])

    def test_stops_when_a_step_does_not_complete(self):
        code = self.run_all(["y"], completes=())
        self.assertEqual(self.calls, [("discover",)])
        self.assertNotEqual(code, 0)

    def test_optional_server_runs_before_the_gate(self):
        (self.tmp / "phase1_step3_handoff.json").write_text("{}")
        (self.tmp / "phase2_step2_demand_test_plan.json").write_text(json.dumps({"started_at": "now"}))
        self.run_all(["y", "y"], completes=())
        self.assertEqual(self.calls, [("validate", "serve"), ("validate", "gate")])

    def test_unpublished_plan_is_not_done(self):
        (self.tmp / "phase1_step3_handoff.json").write_text("{}")
        (self.tmp / "phase2_step2_demand_test_plan.json").write_text(json.dumps({"started_at": None}))
        self.assertEqual(workflow.next_step().args, ("plan",))

    def test_stop_gate_halts_the_run(self):
        (self.tmp / "phase1_step3_handoff.json").write_text("{}")
        (self.tmp / "phase2_step2_demand_test_plan.json").write_text(json.dumps({"started_at": "now"}))
        (self.tmp / "phase2_gate_demand_test.md").write_text("**Outcome:** STOP\n")
        self.run_all([])
        self.assertEqual(self.calls, [])


class EnvTest(unittest.TestCase):
    def test_env_file_fills_gaps_but_never_overrides(self):
        f = Path(tempfile.mkdtemp()) / ".env"
        f.write_text("# comment\nWF_TEST_A=from_file\nexport WF_TEST_B='quoted'\n")
        with mock.patch.dict("os.environ", {"WF_TEST_A": "from_env"}, clear=False):
            workflow.load_env(f)
            import os
            self.assertEqual(os.environ["WF_TEST_A"], "from_env")
            self.assertEqual(os.environ["WF_TEST_B"], "quoted")


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "outputs").mkdir()
        (self.root / "outputs" / "phase1_step3_handoff.json").write_text("{}")
        (self.root / "runtime").mkdir()
        (self.root / "runtime" / "store.db").write_text("x")
        (self.root / "decision_log.json").write_text("[]")
        for name, value in {"ROOT": self.root, "OUT": self.root / "outputs", "LOG": self.root / "decision_log.json",
                            "ARCHIVE": self.root / "runs"}.items():
            patcher = mock.patch.object(workflow, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {"RUNTIME_DIR": ""})
        env.start()
        self.addCleanup(env.stop)

    def reset(self, answer):
        with redirect_stdout(io.StringIO()):
            workflow.reset(input_fn=lambda _: answer)

    def test_cancel_changes_nothing(self):
        self.reset("c")
        self.assertTrue((self.root / "outputs" / "phase1_step3_handoff.json").exists())
        self.assertTrue((self.root / "decision_log.json").exists())

    def test_archive_moves_everything(self):
        self.reset("a")
        self.assertFalse((self.root / "outputs").exists())
        self.assertFalse((self.root / "decision_log.json").exists())
        (run,) = (self.root / "runs").iterdir()
        self.assertTrue((run / "outputs" / "phase1_step3_handoff.json").exists())
        self.assertTrue((run / "runtime" / "store.db").exists())
        self.assertTrue((run / "decision_log.json").exists())

    def test_delete_removes_everything(self):
        self.reset("d")
        for name in ("outputs", "runtime", "decision_log.json", "runs"):
            self.assertFalse((self.root / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
