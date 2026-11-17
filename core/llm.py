"""LLM clients: Ollama (local, open-weight, $0) and a deterministic FakeLLM for tests and structural checks.

The fake is only for tests/compare --fake-llm; runs made with it are never written to outputs/.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass

FAKE_MODEL = "fake-llm"


@dataclass
class LLMResult:
    data: dict
    model: str
    prompt_tokens: int
    output_tokens: int

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class LLMError(RuntimeError):
    pass


class OllamaClient:
    """Calls Ollama's /api/chat with a JSON-schema `format` (structured outputs)."""

    def __init__(self, host: str | None = None, timeout: int = 600):
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout = timeout

    def complete(self, *, model: str, system: str, prompt: str, schema: dict,
                 temperature: float = 0, num_ctx: int = 8192, seed: int = 42) -> LLMResult:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": schema,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": temperature, "num_ctx": num_ctx, "seed": seed},
        }
        if model.startswith("qwen3"):
            body["think"] = False
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except Exception as exc:  # network/model errors become step failures upstream
            raise LLMError(f"ollama call failed: {exc}") from exc
        content = payload.get("message", {}).get("content", "")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {content[:200]!r}") from exc
        return LLMResult(data, model, int(payload.get("prompt_eval_count", 0)), int(payload.get("eval_count", 0)))


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


class FakeLLM:
    """Builds a schema-valid object deterministically. Claims cite the URLs listed after 'SOURCES'."""

    def complete(self, *, model: str, system: str, prompt: str, schema: dict, **_) -> LLMResult:
        src_part = prompt.split("SOURCES", 1)[1] if "SOURCES" in prompt else ""
        urls = list(dict.fromkeys(_URL_RE.findall(src_part)))[:5]
        m = re.search(r"^FEATURES TO COVER[^:]*:\s*(.+)$", prompt, re.MULTILINE)
        self._features = [f.strip() for f in m.group(1).split(";") if f.strip()] if m else []
        data = self._value(schema, "root", urls)
        return LLMResult(data, FAKE_MODEL, len(prompt) // 4, len(json.dumps(data)) // 4)

    def _value(self, schema: dict, name: str, urls: list[str]):
        if name == "sections" and getattr(self, "_features", None):
            return [{"heading": f, "body": f"How {f} works."} for f in self._features]
        t = schema.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), "null")
        if "enum" in schema:
            return schema["enum"][0]
        if t == "object":
            props = schema.get("properties", {})
            return {k: self._value(v, k, urls) for k, v in props.items()}
        if t == "array":
            items = schema.get("items", {})
            if name == "claims":
                claims = [{"text": f"Sourced observation {i + 1}", "source_url": u} for i, u in enumerate(urls)]
                claims.append({"text": "Unsourced estimate of segment size", "source_url": None})
                return claims
            n = max(int(schema.get("minItems", 0)), 2)
            return [self._value(items, f"{name}_{i}", urls) for i in range(n)]
        if t == "integer":
            return int(schema.get("minimum", 3)) if name != "score" else 3
        if t == "number":
            return 19.0 if "price" in name else float(schema.get("minimum", 1))
        if t == "boolean":
            return True
        return f"Example {name.replace('_', ' ')}"


def make_client(fake: bool):
    return FakeLLM() if fake else OllamaClient()
