"""The QA runner executes acceptance tests with no network."""

from venture_builder.qa import run_acceptance


def test_sandbox_blocks_network(tmp_path):
    (tmp_path / "test_acceptance.py").write_text(
        "import socket, unittest\n"
        "class T(unittest.TestCase):\n"
        "    def test_net(self):\n"
        "        s = socket.socket(); s.settimeout(2); s.connect(('1.1.1.1', 80))\n")
    report = run_acceptance(tmp_path)
    assert report["total"] == 1 and report["passed"] == 0
    assert "blocked" in report["output"] or "Network is unreachable" in report["output"] or "Errno" in report["output"]


def test_sandbox_runs_passing_tests(tmp_path):
    (tmp_path / "test_acceptance.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
    report = run_acceptance(tmp_path)
    assert report == report | {"total": 1, "passed": 1}
