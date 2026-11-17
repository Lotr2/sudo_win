"""Model-agnostic chat client: OpenAI-compatible /v1/chat/completions through the Router.

Works with llama-server, vLLM and Ollama. Model quirks live in core/adapters; endpoints in config/endpoints.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.adapters import get_adapter, load_models
from core.router import RequestError, Router, RouterError


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str            # final answer only (reasoning removed by the adapter)
    reasoning: str
    model: str
    endpoint: str
    prompt_tokens: int
    output_tokens: int
    finish_reason: str

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class ChatClient:
    def __init__(self, router: Router, models_cfg: dict | None = None, label: str = ""):
        self.router = router
        self.models_cfg = load_models() if models_cfg is None else models_cfg
        self.label = label          # "fake" for the test endpoint, so logs never pass a fake run off as a real model

    def complete(self, *, model: str, system: str, prompt: str, max_tokens: int, temperature: float = 0,
                 seed: int = 42, json_schema: dict | None = None) -> LLMResult:
        ad = get_adapter(model, self.models_cfg)
        body = ad.build_request(system=system, prompt=prompt, max_tokens=max_tokens, temperature=temperature,
                                seed=seed, json_schema=json_schema)
        try:
            data, endpoint = self.router.post(model, "chat/completions", body)
        except (RouterError, RequestError) as exc:
            raise LLMError(str(exc)) from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("server returned no choices")
        final, reasoning = ad.extract(choices[0])
        usage = data.get("usage") or {}
        name = f"{model} ({self.label})" if self.label else model
        return LLMResult(final, reasoning, name, endpoint, int(usage.get("prompt_tokens", 0)),
                         int(usage.get("completion_tokens", 0)), ad.finish_reason(choices[0]))


def make_client(fake: bool = False, faults: dict | None = None) -> ChatClient:
    if fake:
        from core.fake_llm import fake_router
        return ChatClient(fake_router(faults), label="fake")
    return ChatClient(Router.from_config())
