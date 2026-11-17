"""Model clients behind one interface: Ollama's native /api/chat (GPT-OSS-20B) and the Gemini API's
generateContent (Gemma 4 31B). Tests inject a fake client through the router.

Retries cover transient network errors, HTTP 5xx and HTTP 429 only. Invalid model output is never
retried here; the router handles that (one retry, then a loud failure).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import httpx

from .rate_limit import RateLimiter


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    meta: dict = field(default_factory=dict)


class ChatClient(Protocol):
    def chat(self, *, role: str, model: str, system: str, user: str, schema: dict,
             effort: str | None, num_ctx: int) -> ChatResult: ...


def _with_retries(send: Callable[[], httpx.Response], attempts: int = 5,
                  on_429: Callable[[httpx.Response], float] | None = None,
                  sleep=time.sleep) -> httpx.Response:
    delay = 2.0
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = send()
        except httpx.TransportError as exc:  # connection refused, timeout, DNS
            last = exc
        else:
            if resp.status_code == 429:
                wait = on_429(resp) if on_429 else delay
                last = ModelUnavailable(f"rate limited (HTTP 429), waited {wait:.0f}s")
                sleep(wait)
                continue
            if resp.status_code >= 500:
                last = ModelUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                return resp
        if attempt < attempts - 1:
            sleep(delay)
            delay = min(delay * 2, 60)
    raise ModelUnavailable(f"model server unreachable after {attempts} attempts: {last}")


# ---------------- Ollama ----------------

def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class OllamaClient:
    """GPT-OSS-20B through Ollama's native API. Reasoning effort uses `think: low|medium|high` when the
    server supports it (Ollama >= 0.11), otherwise `Reasoning: <effort>` in the system prompt."""

    def __init__(self, host: str, timeout: float = 600.0, sleep=time.sleep):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._sleep = sleep
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))
        self._think_supported: bool | None = None

    def version(self) -> str:
        resp = _with_retries(lambda: self._http.get(f"{self.host}/api/version"), attempts=2, sleep=self._sleep)
        resp.raise_for_status()
        return resp.json().get("version", "0")

    def tags(self) -> list[str]:
        resp = _with_retries(lambda: self._http.get(f"{self.host}/api/tags"), attempts=2, sleep=self._sleep)
        resp.raise_for_status()
        return [m.get("name") or m.get("model") for m in resp.json().get("models", [])]

    def _think_ok(self) -> bool:
        if self._think_supported is None:
            try:
                self._think_supported = _version_tuple(self.version()) >= (0, 11)
            except Exception:
                self._think_supported = False
        return self._think_supported

    def chat(self, *, role, model, system, user, schema, effort, num_ctx) -> ChatResult:
        think_method = "none"
        sys_text = system
        body: dict = {
            "model": model,
            "stream": False,
            "format": schema,
            "options": {"num_ctx": num_ctx, "temperature": 0.3},
        }
        if effort:
            if self._think_ok():
                body["think"] = effort
                think_method = "think_param"
            else:
                sys_text = f"Reasoning: {effort}\n\n{system}"
                think_method = "system_prompt"
        body["messages"] = [{"role": "system", "content": sys_text}, {"role": "user", "content": user}]
        resp = _with_retries(lambda: self._http.post(f"{self.host}/api/chat", json=body), sleep=self._sleep)
        if resp.status_code == 400 and "think" in resp.text.lower() and "think" in body:
            # Server rejected the think parameter: fall back to the system prompt and remember it.
            self._think_supported = False
            body.pop("think")
            body["messages"][0]["content"] = f"Reasoning: {effort}\n\n{system}"
            think_method = "system_prompt"
            resp = _with_retries(lambda: self._http.post(f"{self.host}/api/chat", json=body), sleep=self._sleep)
        if resp.status_code != 200:
            raise ModelUnavailable(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return ChatResult(
            text=data.get("message", {}).get("content", ""),
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            meta={"think_method": think_method},
        )


# ---------------- Gemini API (Gemma) ----------------

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_KEEP = {"type", "properties", "required", "items", "enum", "description", "nullable", "minimum", "maximum"}


def to_gemini_schema(schema: dict) -> dict:
    """Convert a pydantic JSON schema to the OpenAPI subset the Gemini API accepts:
    inline $refs, turn Optional (anyOf with null) into nullable, uppercase types, drop the rest."""
    defs = schema.get("$defs", {})

    def conv(node: dict) -> dict:
        if "$ref" in node:
            return conv(defs[node["$ref"].split("/")[-1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            out = conv(options[0]) if options else {"type": "STRING"}
            if len(options) < len(node["anyOf"]):
                out["nullable"] = True
            if "description" in node:
                out["description"] = node["description"]
            return out
        if "const" in node:
            node = {**node, "enum": [node["const"]], "type": node.get("type", "string")}
        out: dict = {}
        for key, value in node.items():
            if key not in _KEEP:
                continue
            if key == "type":
                out["type"] = str(value).upper()
            elif key == "properties":
                out["properties"] = {k: conv(v) for k, v in value.items()}
            elif key == "items":
                out["items"] = conv(value)
            else:
                out[key] = value
        if out.get("type") == "OBJECT" and not out.get("properties"):
            out = {"type": "STRING", "description": "JSON object encoded as a string"}
        if "type" not in out and "enum" in out:
            out["type"] = "STRING"
        if "type" not in out:
            out["type"] = "STRING"
        return out

    return conv(schema)


def _retry_delay(resp: httpx.Response) -> float:
    """Seconds to wait from a 429 response: RetryInfo.retryDelay or Retry-After, else 30s."""
    try:
        for detail in resp.json().get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if delay:
                return min(float(str(delay).rstrip("s")) + 1, 120)
    except Exception:
        pass
    after = resp.headers.get("retry-after")
    return min(float(after), 120) if after and after.replace(".", "").isdigit() else 30.0


class GeminiClient:
    """Gemma 4 31B through the Gemini API, with the free-tier rate limiter. The key is sent only in the
    `x-goog-api-key` header to Google and is never logged."""

    def __init__(self, api_key: str, limiter: RateLimiter, timeout: float = 180.0, sleep=time.sleep):
        self._key = api_key
        self.limiter = limiter
        self._sleep = sleep
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0),
                                  headers={"x-goog-api-key": api_key})
        self._schema_mode_ok = True

    def __repr__(self) -> str:
        return "GeminiClient(key=***)"

    def list_models(self) -> list[dict]:
        resp = _with_retries(lambda: self._http.get(f"{GEMINI_BASE}/models", params={"pageSize": 200}),
                             attempts=2, sleep=self._sleep)
        if resp.status_code != 200:
            raise ModelUnavailable(f"Gemini API HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("models", [])

    def chat(self, *, role, model, system, user, schema, effort, num_ctx) -> ChatResult:
        method = "response_schema" if self._schema_mode_ok else "schema_in_prompt"

        def body_for(mode: str) -> dict:
            text = user
            gen: dict = {"responseMimeType": "application/json", "temperature": 0.4}
            if mode == "response_schema":
                gen["responseSchema"] = to_gemini_schema(schema)
            else:
                text = f"{user}\n\nReply with JSON only, matching this JSON schema:\n{json.dumps(schema)}"
            return {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": gen,
            }

        body = body_for(method)
        est = (len(system) + len(json.dumps(body["contents"]))) // 4 + 800
        url = f"{GEMINI_BASE}/models/{model}:generateContent"

        def send():
            self.limiter.acquire(est)
            return self._http.post(url, json=body)

        resp = _with_retries(send, on_429=_retry_delay, sleep=self._sleep)
        if resp.status_code == 400 and method == "response_schema":
            # The model rejected the response schema: put the schema in the prompt instead, and remember it.
            self._schema_mode_ok = False
            method = "schema_in_prompt"
            body = body_for(method)
            resp = _with_retries(send, on_429=_retry_delay, sleep=self._sleep)
        if resp.status_code != 200:
            raise ModelUnavailable(f"Gemini API HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data.get("usageMetadata", {})
        total = int(usage.get("totalTokenCount") or 0)
        self.limiter.record_actual(est, total)
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return ChatResult(
            text=text,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
            meta={"structured_output": method},
        )


# ---------------- fake (tests only) ----------------

class FakeClient:
    """Returns fixtures from a responder function; used by tests so they run without any model."""

    def __init__(self, responder: Callable[..., dict | str]):
        self.responder = responder
        self.calls: list[dict] = []

    def chat(self, *, role, model, system, user, schema, effort, num_ctx) -> ChatResult:
        self.calls.append({"role": role, "model": model, "user": user, "effort": effort})
        out = self.responder(role=role, model=model, system=system, user=user, schema=schema)
        text = out if isinstance(out, str) else json.dumps(out)
        return ChatResult(text=text, prompt_tokens=len(user) // 4, completion_tokens=len(text) // 4,
                          meta={"fake": True})
