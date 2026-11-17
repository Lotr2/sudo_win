"""gpt-oss: reasoning effort (cannot be switched off) and harmony-format final-channel extraction."""
from __future__ import annotations

import re

from core.adapters.base import Adapter

_SEG = re.compile(r"<\|channel\|>(\w+)(?:\s+[^<]*)?<\|message\|>(.*?)(?=<\|end\|>|<\|return\|>|<\|call\|>|<\|start\|>|$)", re.S)


class GptOssAdapter(Adapter):
    kind = "gpt_oss"

    def build_request(self, **kw) -> dict:
        body = super().build_request(**kw)
        effort = self.options.get("reasoning_effort", "low")
        body["reasoning_effort"] = effort                              # vLLM / OpenAI-style servers
        body["chat_template_kwargs"] = {"reasoning_effort": effort}   # llama.cpp --jinja template
        return body

    def extract(self, choice: dict) -> tuple[str, str]:
        final, reasoning = super().extract(choice)
        if "<|channel|>" in final:   # raw harmony text: keep only the final channel
            segs = _SEG.findall(final)
            finals = [t for ch, t in segs if ch == "final"]
            reasoning = reasoning or "\n".join(t for ch, t in segs if ch != "final")
            final = finals[-1] if finals else ""
        return final.strip(), reasoning
