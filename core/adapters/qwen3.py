"""Qwen3: thinking on/off via chat-template kwargs; <think> blocks are stripped from the answer."""
from __future__ import annotations

import re

from core.adapters.base import Adapter

_THINK = re.compile(r"<think>(.*?)</think>", re.S)


class Qwen3Adapter(Adapter):
    kind = "qwen3"

    def build_request(self, **kw) -> dict:
        body = super().build_request(**kw)
        body["chat_template_kwargs"] = {"enable_thinking": bool(self.options.get("thinking", False))}
        return body

    def extract(self, choice: dict) -> tuple[str, str]:
        final, reasoning = super().extract(choice)
        thoughts = _THINK.findall(final)
        final = _THINK.sub("", final)
        if "<think>" in final:          # unterminated block (truncated reasoning): nothing usable after it
            final = final.split("<think>", 1)[0]
        return final.strip(), reasoning or "\n".join(thoughts)
