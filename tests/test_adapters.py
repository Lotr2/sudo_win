"""Adapters: model quirks in the request, and only the final answer (never the reasoning) comes back."""
from core.adapters import get_adapter
from core.fake_llm import DECOY

MODELS = {"gpt-oss-20b": {"adapter": "gpt_oss", "served_name": "gpt-oss:20b", "reasoning_effort": "low",
                          "sampling": {"temperature": 1.0, "top_p": 1.0}},
          "qwen3-8b": {"adapter": "qwen3", "thinking": False}}


def _choice(content, finish="stop", **msg):
    return {"message": {"content": content, **msg}, "finish_reason": finish}


def test_gpt_oss_request_sets_low_reasoning_and_served_name():
    body = get_adapter("gpt-oss-20b", MODELS).build_request(system="s", prompt="p", max_tokens=100, seed=1)
    assert body["model"] == "gpt-oss:20b" and body["reasoning_effort"] == "low"
    assert body["chat_template_kwargs"] == {"reasoning_effort": "low"}


def test_gpt_oss_extracts_final_channel_only():
    raw = (f"<|channel|>analysis<|message|>think {DECOY}<|end|><|start|>assistant"
           '<|channel|>final<|message|>{"ok": 1}<|return|>')
    final, reasoning = get_adapter("gpt-oss-20b", MODELS).extract(_choice(raw))
    assert final == '{"ok": 1}' and "WRONG" in reasoning


def test_gpt_oss_separate_reasoning_field():
    final, reasoning = get_adapter("gpt-oss-20b", MODELS).extract(_choice('{"ok": 1}', reasoning_content="thoughts"))
    assert final == '{"ok": 1}' and reasoning == "thoughts"


def test_qwen3_strips_think_and_disables_thinking():
    ad = get_adapter("qwen3-8b", MODELS)
    assert ad.build_request(system="s", prompt="p", max_tokens=10, seed=1)["chat_template_kwargs"] == {"enable_thinking": False}
    final, _ = ad.extract(_choice(f"<think>{DECOY}</think>\n{{\"ok\": 1}}"))
    assert final == '{"ok": 1}'


def test_finish_reason_and_unknown_model():
    ad = get_adapter("some-other-model", MODELS)
    assert ad.kind == "generic" and ad.finish_reason(_choice("x", "max_tokens")) == "length"


def test_sampling_model_default_then_caller_override():
    ad = get_adapter("gpt-oss-20b", MODELS)
    body = ad.build_request(system="s", prompt="p", max_tokens=10, seed=1)
    assert body["temperature"] == 1.0 and body["top_p"] == 1.0          # the model maker's recommendation
    body = ad.build_request(system="s", prompt="p", max_tokens=10, seed=1, sampling={"temperature": 0.5})
    assert body["temperature"] == 0.5 and body["top_p"] == 1.0          # per-role override wins, rest kept
