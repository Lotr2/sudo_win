"""web_search through the self-hosted SearXNG instance (JSON API; `search.formats` must include json).

Public search engines often block SearXNG (CAPTCHA, rate limits). SEARXNG_ENGINES picks the engines to ask
first; when a search still comes back empty, it is retried once on SEARXNG_FALLBACK_ENGINES, so a blocked
default engine does not leave the research with no sources at all."""

from __future__ import annotations

import re

import httpx

# Placeholder snippets that carry no information (the page hides its description).
EMPTY_SNIPPETS = ("site owner hides the web page description", "we would like to show you a description here",
                  "no information is available for this page")
STOP = {"the", "and", "for", "with", "how", "what", "why", "who", "best", "find", "app", "near", "from", "your", "you",
        "are", "can", "does", "reddit", "forum", "review", "reviews", "price", "prices", "complaints", "problem",
        "alternatives", "help", "this", "that", "into", "about", "free", "online", "tool", "guide", "template", "pack"}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{3,}", text.lower()) if w not in STOP}


def relevant(query: str, title: str, content: str) -> bool:
    """Keep a result only if it shares a meaningful word with the query (drops unrelated junk results)."""
    if any(e in (content or "").lower() for e in EMPTY_SNIPPETS) and not title:
        return False
    q = _terms(query)
    if not q:
        return True
    return bool(q & _terms(f"{title} {content}"))


class SearchUnavailable(RuntimeError):
    pass


class SearxSearch:
    def __init__(self, base_url: str, timeout: float = 20.0, engines: str = "", fallback_engines: str = "bing,yandex"):
        self.base_url = base_url.rstrip("/")
        self.engines = engines.strip()
        self.fallback_engines = fallback_engines.strip()
        self._http = httpx.Client(timeout=timeout, headers={"User-Agent": "venture-builder/0.1"})

    def _query(self, query: str, engines: str) -> list[dict]:
        # Queries in another script (e.g. Arabic) search in every language; English ones stay in English.
        lang = "en" if query.isascii() else "all"
        params = {"q": query, "format": "json", "language": lang, "safesearch": 1}
        if engines:
            params["engines"] = engines
        try:
            resp = self._http.get(f"{self.base_url}/search", params=params)
        except httpx.HTTPError as exc:
            raise SearchUnavailable(f"SearXNG unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise SearchUnavailable(f"SearXNG HTTP {resp.status_code} (is the json format enabled?)")
        return resp.json().get("results", [])

    def __call__(self, query: str, limit: int = 5) -> list[dict]:
        results = self._query(query, self.engines)
        if not results and self.fallback_engines and self.fallback_engines != self.engines:
            results = self._query(query, self.fallback_engines)
        out, seen = [], set()
        for r in results:
            url = r.get("url")
            if not url or not url.startswith("http") or url in seen:
                continue
            if not relevant(query, r.get("title", ""), r.get("content", "")):
                continue
            content = r.get("content", "")
            if any(e in content.lower() for e in EMPTY_SNIPPETS):
                r = {**r, "content": ""}          # keep the page (it may be fetched) but not the empty snippet
            seen.add(url)
            out.append({"url": url, "title": r.get("title", ""), "content": r.get("content", "")})
            if len(out) >= limit:
                break
        return out
