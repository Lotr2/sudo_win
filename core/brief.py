"""Founder brief: idea plus skills, budget, time, target market and values (markdown '- key: value' lines)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("id", "idea", "skills", "budget", "time", "target_market", "values")


@dataclass
class Brief:
    id: str
    idea: str
    skills: str = ""
    budget: str = ""
    time: str = ""
    target_market: str = ""
    values: str = ""
    text: str = ""
    path: str = ""

    def short(self) -> str:
        return "\n".join(f"- {f}: {getattr(self, f)}" for f in FIELDS if getattr(self, f))


def parse_brief(text: str, path: str = "") -> Brief:
    fields = {}
    for m in re.finditer(r"^\s*[-*]\s*([A-Za-z _]+):\s*(.+?)\s*$", text, re.MULTILINE):
        key = m.group(1).strip().lower().replace(" ", "_")
        if key in FIELDS:
            fields[key] = m.group(2)
    if not fields.get("id") or not fields.get("idea"):
        raise ValueError("brief needs at least '- id:' and '- idea:' lines")
    if not re.fullmatch(r"[a-z0-9]+", fields["id"]):
        raise ValueError("brief id must be lowercase letters/digits (it becomes part of output file names)")
    return Brief(text=text, path=path, **fields)


def load_brief(path: str | Path) -> Brief:
    p = Path(path)
    return parse_brief(p.read_text(encoding="utf-8"), str(p))
