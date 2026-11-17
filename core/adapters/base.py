"""Adapter interface. An adapter turns a call into a request body and a raw choice into (final, reasoning)."""
from __future__ import annotations


class Adapter:
    kind = "generic"

    def __init__(self, model: str, served_name: str | None = None, api_json_schema: bool = False, **options):
        self.model = model
        self.served_name = served_name or model
        self.api_json_schema = api_json_schema
        self.options = options

    def build_request(self, *, system: str, prompt: str, max_tokens: int, temperature: float, seed: int,
                      json_schema: dict | None = None) -> dict:
        body = {
            "model": self.served_name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": int(max_tokens),
            "temperature": temperature,
            "seed": seed,
            "stream": False,
        }
        if json_schema is not None and self.api_json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": json_schema}}
        return body

    def extract(self, choice: dict) -> tuple[str, str]:
        """Return (final_answer, reasoning). Only the final answer is ever parsed."""
        msg = choice.get("message") or {}
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        return (msg.get("content") or "").strip(), reasoning

    @staticmethod
    def finish_reason(choice: dict) -> str:
        fr = (choice.get("finish_reason") or "stop").lower()
        return "length" if fr in ("length", "max_tokens") else fr


class GenericAdapter(Adapter):
    kind = "generic"
