"""Forgiving parser for model answers: metadata JSON, then ---CONTENT--- and plain text (code lives only there).

Order: split content off, strip fences, take the first balanced {...}, repair common slips, then validate.
Callers feed the ParseError message back to the model for one retry.
"""
from __future__ import annotations

import ast
import json
import re

import jsonschema

DELIM = "---CONTENT---"
_CLAIM_SRC = re.compile(r"\s*\[source:\s*(\S+?)\]\s*$", re.I)
_CLAIM_ASSUMPTION = re.compile(r"\s*\[assumption\]\s*$", re.I)


class ParseError(ValueError):
    pass


def split_content(text: str) -> tuple[str, str]:
    if DELIM in text:
        head, content = text.split(DELIM, 1)
        return head, content.strip("\n")
    return text, ""


def _first_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text)
    raw = _first_object(text)
    if raw is None:
        raise ParseError("no JSON object found; start the answer with a JSON object")
    candidates = [raw]
    fixed = re.sub(r",\s*([}\]])", r"\1", raw)
    fixed = fixed.replace("“", '"').replace("”", '"').replace("’", "'")
    candidates.append(fixed)
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    try:  # single quotes / Python literals
        obj = ast.literal_eval(re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", fixed))))
        if isinstance(obj, dict):
            return obj
    except (ValueError, SyntaxError):
        pass
    raise ParseError("the JSON object is not valid JSON (check quotes, commas and brackets)")


def parse_answer(text: str, schema: dict, require_content: bool) -> tuple[dict, str]:
    head, content = split_content(text)
    data = extract_json(head)
    data = {k: v for k, v in data.items() if k in schema.get("properties", data)}   # drop unknown keys
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ParseError(f"JSON field {where}: {exc.message}") from exc
    if require_content and not content.strip():
        raise ParseError(f"missing content: after the JSON write a line {DELIM} and then the content")
    return data, content


def parse_claims(items) -> list[dict]:
    """'text [source: URL]' -> {text, source_url}; 'text [ASSUMPTION]' or no tag -> source_url None."""
    out = []
    for s in items or []:
        s = str(s).strip()
        m = _CLAIM_SRC.search(s)
        if m:
            out.append({"text": s[:m.start()].strip(), "source_url": m.group(1).rstrip(".,;")})
        else:
            out.append({"text": _CLAIM_ASSUMPTION.sub("", s).strip(), "source_url": None})
    return out


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.5) + 1
