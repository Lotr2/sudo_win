"""Spreads LLM calls over OpenAI-compatible endpoints (one per GPU PC).

Routing: the endpoint serving the model with the fewest in-flight requests (relative to its slots).
Each endpoint takes at most `slots` calls at once. A connection error, timeout or 5xx marks the
endpoint down for `down_for_s` and the call fails over to another endpoint. 4xx errors are request
bugs and are not retried elsewhere.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "endpoints.yaml"


class RouterError(RuntimeError):
    """No endpoint could answer (all down, busy past the wait limit, or all failed)."""


class RequestError(RuntimeError):
    """The server rejected the request itself (4xx); failing over would not help."""


@dataclass
class Endpoint:
    name: str
    base_url: str
    models: list = field(default_factory=lambda: ["*"])
    slots: int = 4
    timeout_s: float = 300
    in_flight: int = 0
    served: int = 0
    failures: int = 0
    down_until: float = 0.0

    def serves(self, model: str) -> bool:
        return "*" in self.models or model in self.models

    def url(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")


def http_transport(url: str, body: dict | None, timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code not in (408, 429):
            raise RequestError(f"HTTP {exc.code}: {exc.read()[:300]!r}") from exc
        raise


def _norm(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "http://" + url
    return url if url.endswith("/v1") else url + "/v1"


class Router:
    def __init__(self, endpoints: list[Endpoint], transport=http_transport, down_for_s: float = 30,
                 wait_for_slot_s: float = 600):
        if not endpoints:
            raise RouterError("no endpoints configured")
        self.endpoints = endpoints
        self.transport = transport
        self.down_for_s = down_for_s
        self.wait_for_slot_s = wait_for_slot_s
        self._cv = threading.Condition()

    @classmethod
    def from_config(cls, path: Path | None = None, env=os.environ, transport=http_transport) -> "Router":
        cfg = yaml.safe_load(Path(path or CONFIG_FILE).read_text(encoding="utf-8")) or {}
        slots = int(env.get("LLM_SLOTS", 0) or 0)
        timeout = float(env.get("LLM_TIMEOUT_S", 0) or 0)
        if env.get("LLM_ENDPOINTS"):
            eps = []
            items = [x.strip() for x in env["LLM_ENDPOINTS"].split(",") if x.strip()]
            for i, item in enumerate(items):
                if "=" in item and not item.startswith("http"):
                    name, url = item.split("=", 1)
                else:
                    name, url = f"gpu{i + 1}", item
                eps.append(Endpoint(name.strip(), _norm(url), ["*"], slots or 4, timeout or 300))
        else:
            eps = [Endpoint(e["name"], _norm(e["base_url"]), list(e.get("models", ["*"])),
                            slots or int(e.get("slots", 4)), timeout or float(e.get("timeout_s", 300)))
                   for e in cfg.get("endpoints", [])]
        return cls(eps, transport, float(cfg.get("down_for_s", 30)), float(cfg.get("wait_for_slot_s", 600)))

    @property
    def capacity(self) -> int:
        now = time.time()
        return max(1, sum(e.slots for e in self.endpoints if e.down_until <= now))

    def _acquire(self, model: str, exclude: set) -> Endpoint | None:
        deadline = time.time() + self.wait_for_slot_s
        with self._cv:
            while True:
                now = time.time()
                serving = [e for e in self.endpoints if e.serves(model) and e.name not in exclude]
                if not serving:
                    return None
                up = [e for e in serving if e.down_until <= now]
                free = [e for e in up if e.in_flight < e.slots]
                if free:
                    ep = min(free, key=lambda e: (e.in_flight / e.slots, e.served))
                    ep.in_flight += 1
                    ep.served += 1
                    return ep
                if now >= deadline:
                    raise RouterError(f"no free slot for {model} within {self.wait_for_slot_s:.0f}s")
                wake = min([e.down_until for e in serving if e.down_until > now] + [deadline, now + 1])
                self._cv.wait(timeout=max(0.05, wake - now))

    def _release(self, ep: Endpoint, failed: bool) -> None:
        with self._cv:
            ep.in_flight -= 1
            if failed:
                ep.failures += 1
                ep.down_until = time.time() + self.down_for_s
            self._cv.notify_all()

    def post(self, model: str, path: str, body: dict) -> tuple[dict, str]:
        """POST to the best endpoint for `model`; fail over on endpoint errors. Returns (json, endpoint_name)."""
        tried: set = set()
        last: Exception | None = None
        for _ in range(len(self.endpoints)):
            ep = self._acquire(model, tried)
            if ep is None:
                break
            failed = False
            try:
                return self.transport(ep.url(path), body, ep.timeout_s), ep.name
            except RequestError:
                raise
            except Exception as exc:  # connection refused, timeout, 5xx, bad JSON from a dying server
                failed, last = True, exc
                tried.add(ep.name)
            finally:
                self._release(ep, failed)
        raise RouterError(f"all endpoints failed for {model}: {last}")

    def health(self) -> dict[str, bool]:
        out = {}
        for e in self.endpoints:
            try:
                self.transport(e.url("models"), None, 5)
                out[e.name] = True
            except Exception:
                out[e.name] = False
        return out
