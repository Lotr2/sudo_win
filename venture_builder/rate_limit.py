"""Rate limiter for the Gemini API free tier (Gemma 4 31B).

Three limits, all configurable in `.env`: requests per minute (GEMMA_RPM), tokens per minute
(GEMMA_TPM) and requests per day (GEMMA_RPD). The minute limits use a sliding 60-second window; the
daily count is stored on disk so it survives restarts. When the daily limit is reached the limiter
raises instead of waiting, so the run fails loudly and the founder can switch to GEMMA_ROLES_ON_OSS=1.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class DailyLimitReached(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, rpm: int, tpm: int, rpd: int, state_path: Path | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm, self.rpd = rpm, tpm, rpd
        self.state_path = state_path
        self._clock, self._sleep = clock, sleep
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()
        self.waited_seconds = 0.0

    # ---- daily counter (UTC day) ----
    def _day(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read_daily(self) -> dict:
        if self.state_path and self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                return {}
        return getattr(self, "_daily_mem", {})

    def _write_daily(self, data: dict) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data))
        else:
            self._daily_mem = data

    def used_today(self) -> int:
        return int(self._read_daily().get(self._day(), 0))

    # ---- minute windows ----
    def _prune(self, now: float) -> None:
        while self._requests and now - self._requests[0] >= 60:
            self._requests.popleft()
        while self._tokens and now - self._tokens[0][0] >= 60:
            self._tokens.popleft()

    def acquire(self, est_tokens: int) -> float:
        """Block until a request of about `est_tokens` tokens fits all limits. Returns seconds waited."""
        est_tokens = max(1, min(est_tokens, self.tpm))
        waited = 0.0
        with self._lock:
            daily = self._read_daily()
            if int(daily.get(self._day(), 0)) >= self.rpd:
                raise DailyLimitReached(
                    f"Gemini free-tier daily limit of {self.rpd} requests reached; "
                    "set GEMMA_ROLES_ON_OSS=1 to continue on GPT-OSS-20B")
            while True:
                now = self._clock()
                self._prune(now)
                used_tokens = sum(t for _, t in self._tokens)
                wait = 0.0
                if len(self._requests) >= self.rpm:
                    wait = max(wait, 60 - (now - self._requests[0]))
                if used_tokens + est_tokens > self.tpm and self._tokens:
                    wait = max(wait, 60 - (now - self._tokens[0][0]))
                if wait <= 0:
                    break
                self._sleep(wait + 0.05)
                waited += wait + 0.05
            now = self._clock()
            self._requests.append(now)
            self._tokens.append((now, est_tokens))
            daily = {self._day(): int(daily.get(self._day(), 0)) + 1}
            self._write_daily(daily)
        self.waited_seconds += waited
        return waited

    def record_actual(self, est_tokens: int, actual_tokens: int) -> None:
        """Replace the estimate of the most recent matching request with the real token count."""
        with self._lock:
            for i in range(len(self._tokens) - 1, -1, -1):
                ts, tok = self._tokens[i]
                if tok == max(1, min(est_tokens, self.tpm)):
                    self._tokens[i] = (ts, max(actual_tokens, 1))
                    return

    def penalize(self, seconds: float) -> None:
        """Honour a server-side retry delay (HTTP 429)."""
        self._sleep(seconds)
        self.waited_seconds += seconds
