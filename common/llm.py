"""Minimal client for any OpenAI-compatible chat endpoint (Ollama, vLLM, LM Studio, llama.cpp).

Standard library only. Defaults target gpt-oss:20b served locally by Ollama, so no paid API is needed.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "gpt-oss:20b"


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LLMClient:
    base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL))
    model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", DEFAULT_MODEL))
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", "ollama"))
    temperature: float = 0.3
    timeout: float = 300.0
    max_retries: int = 2

    def _post(self, messages: list[dict], json_mode: bool = True) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as e:
            raise LLMError(
                f"Could not reach the model at {self.base_url} ({e}). "
                f"Is Ollama running with `{self.model}` pulled?"
            ) from e

    def chat_code(self, system: str, user: str, langs: tuple[str, ...]) -> tuple[str, LLMResult]:
        """Ask for one fenced code block (more reliable than code escaped inside JSON) and return its contents."""
        raw = self._post([{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=False)
        usage = raw.get("usage") or {}
        content = raw["choices"][0]["message"].get("content") or ""
        result = LLMResult({"text": content}, self.model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        return extract_code(content, langs), result

    def chat_json(self, system: str, user: str, required_keys: tuple[str, ...] = ()) -> LLMResult:
        """Ask for a JSON object; retry with the parse error if the reply is malformed."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt_tokens = completion_tokens = 0
        last_error = ""
        for _ in range(self.max_retries + 1):
            raw = self._post(messages)
            usage = raw.get("usage") or {}
            prompt_tokens += usage.get("prompt_tokens", 0)
            completion_tokens += usage.get("completion_tokens", 0)
            content = raw["choices"][0]["message"].get("content") or ""
            try:
                data = extract_json(content)
                missing = [k for k in required_keys if k not in data]
                if missing:
                    raise ValueError(f"missing keys: {missing}")
                return LLMResult(data, self.model, prompt_tokens, completion_tokens)
            except ValueError as e:
                last_error = str(e)
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That reply was not valid ({e}). Reply with ONLY the JSON object."},
                ]
        raise LLMError(f"Model did not return valid JSON after retries: {last_error}")


def extract_code(text: str, langs: tuple[str, ...]) -> str:
    """Return the largest fenced block tagged with one of `langs`, else the largest untagged block, else the text."""
    blocks = re.findall(r"```([\w+-]*)[ \t]*\n(.*?)```", text, re.DOTALL)
    tagged = [b for lang, b in blocks if lang.lower() in langs]
    pool = tagged or [b for lang, b in blocks if not lang] or [b for _, b in blocks]
    return (max(pool, key=len) if pool else text).strip() + "\n"


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences or stray text around it."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON is not an object")
    return obj
