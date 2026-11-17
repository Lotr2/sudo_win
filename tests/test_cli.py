"""CLI: `venture run` takes the brief from a file, from --idea, or from standard input."""

import argparse
import io

import pytest

from venture_builder import cli


def _args(brief=None, idea=None):
    return argparse.Namespace(brief=brief, idea=idea)


def test_brief_from_idea_text():
    assert cli.read_brief(_args(idea="  A template pack for tutors  ")) == "A template pack for tutors"


def test_brief_from_file(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("A checklist for new landlords\n")
    assert cli.read_brief(_args(brief=str(f))) == "A checklist for new landlords"


def test_brief_from_piped_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Piped idea\nwith two lines\n"))
    assert cli.read_brief(_args()) == "Piped idea\nwith two lines"


def test_file_and_idea_together_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        cli.read_brief(_args(brief="x.md", idea="y"))


def test_progress_events_in_plain_words():
    from venture_builder import labels
    assert labels.event_text({"kind": "step", "node": "p1_research"}) == "Researching the market and competitors"
    assert labels.event_text({"kind": "output", "file": "phase1_gate_decision.md"}) == "New document: Stage 1: Gate report"
    assert labels.event_text({"kind": "output", "file": "phase2_step3_landing.html"}) == "New document: Stage 2: Landing (web page)"
    assert labels.event_text({"kind": "progress", "message": "Search failed: timeout"}).startswith("Web search isn't responding")
    assert labels.event_text({"kind": "chat", "message": "liaison: hi"}) is None


def test_gate_choices_accept_numbers_and_words():
    assert [cli._gate_choice(x) for x in ("1", "2", "3", "4")] == ["go", "repeat", "pivot", "stop"]
    assert cli._gate_choice("Stop here") == "stop" and cli._gate_choice("maybe") is None
