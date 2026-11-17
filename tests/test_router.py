"""Router: spreads calls over endpoints, respects slots, fails over, does not retry request bugs."""
import threading
import time

import pytest

from core.router import Endpoint, RequestError, Router, RouterError


def _eps(slots=2):
    return [Endpoint("a", "http://a/v1", ["*"], slots, 5), Endpoint("b", "http://b/v1", ["*"], slots, 5)]


def test_spreads_concurrent_calls_and_respects_slots():
    lock, active, peak, used = threading.Lock(), {"a": 0, "b": 0}, {"a": 0, "b": 0}, []

    def transport(url, body, timeout):
        host = url.split("//")[1].split("/")[0]
        with lock:
            active[host] += 1
            peak[host] = max(peak[host], active[host])
            used.append(host)
        time.sleep(0.05)
        with lock:
            active[host] -= 1
        return {"ok": True}

    r = Router(_eps(slots=2), transport=transport)
    threads = [threading.Thread(target=r.post, args=("m", "chat/completions", {})) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(used) == {"a", "b"} and len(used) == 8
    assert peak["a"] <= 2 and peak["b"] <= 2


def test_fails_over_and_marks_endpoint_down():
    def transport(url, body, timeout):
        if "//a/" in url:
            raise ConnectionError("down")
        return {"from": "b"}

    r = Router(_eps(), transport=transport, down_for_s=60)
    for _ in range(3):
        data, name = r.post("m", "chat/completions", {})
        assert name == "b"
    a = r.endpoints[0]
    assert a.failures == 1 and a.down_until > time.time()      # tried once, then skipped while down


def test_all_endpoints_down_raises():
    def transport(url, body, timeout):
        raise TimeoutError("slow")

    with pytest.raises(RouterError):
        Router(_eps(), transport=transport).post("m", "chat/completions", {})


def test_request_errors_are_not_failed_over():
    calls = []

    def transport(url, body, timeout):
        calls.append(url)
        raise RequestError("HTTP 400")

    with pytest.raises(RequestError):
        Router(_eps(), transport=transport).post("m", "chat/completions", {})
    assert len(calls) == 1


def test_routes_only_to_endpoints_serving_the_model():
    eps = [Endpoint("a", "http://a/v1", ["gpt-oss-20b"], 2, 5), Endpoint("b", "http://b/v1", ["qwen3-8b"], 2, 5)]
    r = Router(eps, transport=lambda url, body, t: {"url": url})
    assert r.post("qwen3-8b", "chat/completions", {})[1] == "b"
    with pytest.raises(RouterError):
        r.post("other-model", "chat/completions", {})


def test_env_endpoints_override_config():
    r = Router.from_config(env={"LLM_ENDPOINTS": "pc1=http://10.0.0.1:8080,http://10.0.0.2:8080/v1", "LLM_SLOTS": "3"})
    assert [(e.name, e.base_url, e.slots) for e in r.endpoints] == [
        ("pc1", "http://10.0.0.1:8080/v1", 3), ("gpu2", "http://10.0.0.2:8080/v1", 3)]
