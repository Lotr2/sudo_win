"""web_search through the self-hosted SearXNG instance (JSON API; `search.formats` must include json)."""

from __future__ import annotations

import httpx


class SearchUnavailable(RuntimeError):
    pass


class SearxSearch:
    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, headers={"User-Agent": "venture-builder/0.1"})

    def __call__(self, query: str, limit: int = 5) -> list[dict]:
        try:
            resp = self._http.get(f"{self.base_url}/search",
                                  params={"q": query, "format": "json", "language": "en", "safesearch": 1})
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"SearXNG unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise SearchUnavailable(f"SearXNG HTTP {resp.status_code} (is the json format enabled?)")
        results = resp.json().get("results", [])
        out = []
        for r in results:
            url = r.get("url")
            if not url or not url.startswith("http"):
                continue
            out.append({"url": url, "title": r.get("title", ""), "content": r.get("content", "")})
            if len(out) >= limit:
                break
        return out
