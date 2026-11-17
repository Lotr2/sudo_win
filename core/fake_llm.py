"""Fake OpenAI-compatible endpoints for tests and structural checks (no GPU, deterministic).

They answer in each model's raw format (gpt-oss harmony channels, qwen3 <think>) with a decoy JSON in
the reasoning, so tests prove only the final answer is parsed. Faults can be injected:
{"length": n, "length_if": "<prompt substring>", "invalid_json": n, "docs_drop": n, "down": ["fake-b"],
 "record": <list that receives every request body>}. Answers vary with the seed (seed 42 = the baseline answer).
Runs made with these are never written to outputs/.
"""
from __future__ import annotations

import json
import re
import threading

from core.router import Endpoint, Router

_URL = re.compile(r"https?://[^\s)\]>\"']+")
DECOY = '{"title": "WRONG - this JSON is in the reasoning and must be ignored"}'


class FakeTransport:
    def __init__(self, faults: dict | None = None):
        self.faults = dict(faults or {})
        self.down = set(self.faults.pop("down", []))
        self.record = self.faults.pop("record", None)
        self.calls = 0
        self._lock = threading.Lock()

    def _take(self, name: str) -> bool:
        with self._lock:
            if self.faults.get(name, 0) > 0:
                self.faults[name] -= 1
                return True
            return False

    def __call__(self, url: str, body: dict | None, timeout: float) -> dict:
        host = url.split("//", 1)[1].split("/", 1)[0]
        if host in self.down:
            raise ConnectionError(f"fake endpoint {host} is down")
        if body is None:
            return {"data": [{"id": "fake"}]}
        with self._lock:
            self.calls += 1
            if self.record is not None:
                self.record.append(body)
        prompt = body["messages"][-1]["content"]
        final = self._answer(prompt, body.get("seed", 42))
        finish = "stop"
        if self._take("invalid_json"):
            final = "Here is the result: {title: 'unterminated"
        if self._take("length") or (self.faults.get("length_if") and self.faults["length_if"] in prompt):
            final, finish = final[: len(final) // 3], "length"
        model = body.get("model", "")
        if model.startswith("gpt-oss"):
            content = (f"<|channel|>analysis<|message|>Drafting. {DECOY}<|end|>"
                       f"<|start|>assistant<|channel|>final<|message|>{final}<|return|>")
        elif model.startswith("qwen"):
            content = f"<think>Drafting. {DECOY}</think>\n{final}"
        else:
            content = final
        limit = int(body.get("max_tokens") or 0)
        if limit and len(content) // 4 > limit:          # honour max_tokens like a real server
            content, finish = content[: limit * 4], "length"
        return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(content) // 4}}

    # -- deterministic answer built from the prompt's SCHEMA line ---------------------------------
    def _answer(self, prompt: str, seed: int = 42) -> str:
        m = re.search(r"^SCHEMA: (.+)$", prompt, re.MULTILINE)
        schema = json.loads(m.group(1)) if m else {"type": "object", "properties": {}}
        src = prompt.split("SOURCES", 1)[1] if "SOURCES" in prompt else ""
        urls = list(dict.fromkeys(_URL.findall(src)))[:5]
        obj = {k: self._value(k, v, urls) for k, v in schema.get("properties", {}).items()}
        if seed not in (None, 42):          # another sample: change free-text fields so a redo is visibly different
            obj = {k: (f"{v} (sample {seed})" if isinstance(v, str) and k not in ("reason",) else v) for k, v in obj.items()}
        out = json.dumps(obj)
        if "---CONTENT---" in prompt:
            out += "\n---CONTENT---\n" + self._content(prompt)
        return out

    def _content(self, prompt: str) -> str:
        f = re.search(r"^FEATURE: (.+)$", prompt, re.MULTILINE)
        if f:
            name = f.group(1).strip()
            return f"## {name}\n\nHow {name} works.\n\n```python\nprint(\"{{'not': 'json'}}\")\n```\n"
        feats = re.search(r"^FEATURES TO COVER[^:]*:\s*(.+)$", prompt, re.MULTILINE)
        if feats:
            names = [x.strip() for x in feats.group(1).split(";") if x.strip()]
            if self._take("docs_drop"):          # fault: user docs forget the last feature -> MVP tests fail
                names = names[:-1]
            return "\n".join(f"## {x}\n\nHow to use {x}.\n" for x in names)
        return "## Notes\n\nExample content based on the inputs.\n"

    @staticmethod
    def _value(name: str, schema: dict, urls: list[str]):
        t = schema.get("type")
        if t == "array":
            it = (schema.get("items") or {}).get("type")
            if name == "claims":
                return [f"Sourced observation {i + 1} [source: {u}]" for i, u in enumerate(urls)] + \
                       ["Unsourced estimate of segment size [ASSUMPTION]"]
            if it in ("number", "integer"):
                return [19, 19]
            if name == "features":
                return ["Feature 1", "Feature 2"]
            return [f"Example {name} {i + 1}" for i in range(2)]
        if t == "number":
            return 19.0
        if t == "integer":
            return max(1, int(schema.get("minimum", 1)))
        if t == "boolean":
            return True
        return f"Example {name.replace('_', ' ')}"


def fake_router(faults: dict | None = None) -> Router:
    eps = [Endpoint("fake-a", "http://fake-a/v1", ["*"], 2, 30), Endpoint("fake-b", "http://fake-b/v1", ["*"], 2, 30)]
    return Router(eps, transport=FakeTransport(faults), down_for_s=30, wait_for_slot_s=60)
