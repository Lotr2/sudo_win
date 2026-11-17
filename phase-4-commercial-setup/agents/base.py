from __future__ import annotations

import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"  # repo-root prompts/, one file per agent


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def format_brief(brief: dict) -> str:
    return "\n".join(f"- {k.replace('_', ' ').title()}: {v or '(not given)'}" for k, v in brief.items())


def format_history(history: list[dict]) -> str:
    if not history:
        return "(no questions asked yet)"
    return "\n".join(f"Q: {turn['question']}\nA: {turn['answer']}" for turn in history)


def as_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)
