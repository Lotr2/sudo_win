"""Router: routing table, checker never maker, single-model mode, the key never leaks, rate limits."""

import json

import pytest

from conftest import drive
from fakes import FakeSearch, fake_fetch
from venture_builder.config import load_settings
from venture_builder.decision_log import DecisionLog
from venture_builder.llm_clients import FakeClient, to_gemini_schema
from venture_builder.rate_limit import DailyLimitReached, RateLimiter
from venture_builder.router import ROUTES, Router
from venture_builder.runtime import Runtime
from venture_builder.schemas import LeanCanvas, PolicyVerdict


def _router(env, models):
    s = load_settings(env, dotenv=None)
    fake = FakeClient(models)
    return Router(s, oss=fake, gemma=fake), fake


def test_each_role_goes_to_its_model(models):
    r, fake = _router({"GEMINI_API_KEY": "k"}, models)
    for role, route in ROUTES.items():
        if role == "policy_classifier":
            continue
        expected = r.settings.model_oss if route.family == "oss" else r.settings.model_gemma
        assert r.model_name(r.family_for(role)) == expected, role
    assert r.family_for("canvas_builder") != r.family_for("skeptical_investor")
    assert r.family_for("pivot_memo_writer") != r.family_for("critic")
    assert r.family_for("policy_classifier", author_family="gemma") == "oss"
    assert r.family_for("policy_classifier", author_family="oss") == "gemma"


def test_without_key_every_role_runs_on_oss_and_same_model_checks_are_flagged(models):
    r, fake = _router({}, models)
    assert r.settings.single_model
    log = DecisionLog(None)
    log.bind()
    for role in ROUTES:
        assert r.model_name(r.family_for(role)) == r.settings.model_oss
    r.call("policy_classifier", {"context": "outbox", "text": "hello"}, PolicyVerdict, action="classify",
           author_family=r.author_family("landing_copywriter"))
    assert log.entries[-1]["same_model_check"] is True
    assert log.entries[-1]["model"] == r.settings.model_oss


def test_with_key_checks_are_on_the_other_model(models):
    r, fake = _router({"GEMINI_API_KEY": "k"}, models)
    log = DecisionLog(None)
    log.bind()
    r.call("policy_classifier", {"context": "outbox", "text": "hello"}, PolicyVerdict, action="classify",
           author_family=r.author_family("landing_copywriter"))
    assert "same_model_check" not in log.entries[-1]


def test_invalid_output_retried_once_then_fails_loudly(models):
    from venture_builder.router import ModelOutputError
    r, fake = _router({"GEMINI_API_KEY": "k"}, models)
    fake.responder = lambda **kw: "not json"
    log = DecisionLog(None)
    log.bind()
    with pytest.raises(ModelOutputError):
        r.call("brief_parser", "x", LeanCanvas, action="parse")
    assert len(fake.calls) == 2 and log.entries[-1]["action"] == "parse_failed"


def test_key_never_appears_in_any_run_file(tmp_path, models):
    key = "AQ.sentinel-key-that-must-never-leak-123"
    s = load_settings({"RUNS_DIR": str(tmp_path / "runs"), "GEMINI_API_KEY": key}, dotenv=None)
    fake = FakeClient(models)
    rt = Runtime.new(s, "designer brief", router=Router(s, fake, fake), search=FakeSearch(), fetch=fake_fetch)
    drive(rt)
    for path in (tmp_path / "runs").rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes(), path
    assert key not in repr(s) and key not in json.dumps(s.public())


def test_gemini_schema_conversion():
    sch = to_gemini_schema(LeanCanvas.model_json_schema())
    assert sch["type"] == "OBJECT" and sch["properties"]["problem"]["type"] == "OBJECT"
    assert "$ref" not in json.dumps(sch) and "$defs" not in json.dumps(sch)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_rate_limiter_requests_per_minute(tmp_path):
    c = Clock()
    rl = RateLimiter(rpm=2, tpm=100000, rpd=100, state_path=tmp_path / "u.json", clock=c, sleep=c.sleep)
    assert rl.acquire(10) == 0 and rl.acquire(10) == 0
    waited = rl.acquire(10)
    assert waited >= 59


def test_rate_limiter_tokens_per_minute(tmp_path):
    c = Clock()
    rl = RateLimiter(rpm=100, tpm=1000, rpd=100, state_path=tmp_path / "u.json", clock=c, sleep=c.sleep)
    rl.acquire(800)
    assert rl.acquire(500) >= 59


def test_rate_limiter_daily_limit_fails_loudly(tmp_path):
    c = Clock()
    rl = RateLimiter(rpm=100, tpm=100000, rpd=2, state_path=tmp_path / "u.json", clock=c, sleep=c.sleep)
    rl.acquire(1)
    rl.acquire(1)
    with pytest.raises(DailyLimitReached):
        rl.acquire(1)
    assert RateLimiter(100, 100000, 2, state_path=tmp_path / "u.json").used_today() == 2


# ---------------- llama.cpp back end ----------------

def _llamacpp_client(handler):
    import httpx
    from venture_builder.llm_clients import LlamaCppClient
    client = LlamaCppClient("http://llama:8090/v1", sleep=lambda s: None)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_backend_setting_picks_llamacpp_client():
    from venture_builder.llm_clients import LlamaCppClient, OllamaClient
    from venture_builder.router import oss_client
    s = load_settings({"OSS_BACKEND": "llamacpp", "LLAMACPP_URL": "http://h:8090/v1/"}, dotenv=None)
    client = oss_client(s, 5)
    assert isinstance(client, LlamaCppClient) and client.base_url == "http://h:8090"
    assert isinstance(oss_client(load_settings({}, dotenv=None), 5), OllamaClient)


def test_llamacpp_sends_schema_and_effort_and_returns_only_the_answer():
    sent = {}

    def handler(request):
        import httpx
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}', "reasoning_content": "thinking..."}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    result = _llamacpp_client(handler).chat(role="r", model="gpt-oss:20b", system="s", user="u",
                                            schema=schema, effort="high", num_ctx=8192)
    assert sent["url"] == "http://llama:8090/v1/chat/completions"
    assert sent["body"]["response_format"]["json_schema"]["schema"] == schema
    assert sent["body"]["chat_template_kwargs"] == {"reasoning_effort": "high"}
    assert result.text == '{"ok": true}' and result.prompt_tokens == 12 and result.completion_tokens == 3


def test_llamacpp_falls_back_to_json_object_when_schema_rejected():
    import httpx
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="failed to parse JSON schema")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    result = _llamacpp_client(handler).chat(role="r", model="m", system="s", user="u",
                                            schema={"type": "object"}, effort=None, num_ctx=1)
    assert [b["response_format"]["type"] for b in bodies] == ["json_schema", "json_object"]
    assert "matching this JSON schema" in bodies[1]["messages"][1]["content"]
    assert result.meta["structured_output"] == "json_object"
