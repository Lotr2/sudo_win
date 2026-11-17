"""Founder approval, enforced in code. Nothing moves past a gate unless the founder explicitly picks an option."""

from __future__ import annotations


class FounderQuit(Exception):
    """The founder typed /quit."""


def ask(input_fn, prompt: str, allow_empty: bool = False) -> str:
    while True:
        answer = input_fn(prompt).strip()
        if answer.lower() in {"/quit", "/exit"}:
            raise FounderQuit()
        if answer or allow_empty:
            return answer


def choose(input_fn, output_fn, question: str, options: dict[str, str]) -> str:
    """Ask the founder to pick one option by its key. Re-asks until the answer is valid; there is no default."""
    output_fn(question)
    for key, label in options.items():
        output_fn(f"  [{key}] {label}")
    while True:
        answer = ask(input_fn, "> ").lower()
        if answer in options:
            return answer
        output_fn(f"Please type one of: {', '.join(options)}")
