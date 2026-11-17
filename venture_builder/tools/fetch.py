"""fetch_page: fetch a page and return its readable text and quoted snippets.

Fetched pages are data. Text is only quoted; lines that look like instructions to an AI
("ignore previous instructions", "you are now", ...) are dropped before any agent sees them.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "button", "aside"}
INJECTION = re.compile(
    r"ignore (all |any |your |the )?(previous|prior|above)? ?(instructions|rules)|you are now|system prompt|"
    r"disregard (the|your|all)|as an ai|assistant:|developer mode", re.I)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in {"p", "li", "br", "h1", "h2", "h3", "h4", "tr", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def readable_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed pages still yield partial text
        pass
    text = "".join(parser.parts)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) > 30 and not INJECTION.search(ln))


def snippets(text: str, query: str, k: int = 3, max_len: int = 400) -> list[str]:
    """Pick the k passages that share the most words with the query."""
    terms = {w for w in re.findall(r"[a-z]{4,}", query.lower())}
    scored = []
    for para in text.split("\n"):
        words = set(re.findall(r"[a-z]{4,}", para.lower()))
        overlap = len(terms & words)
        if overlap:
            scored.append((overlap, para[:max_len]))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:k]]


class PageFetcher:
    def __init__(self, timeout: float = 15.0, max_bytes: int = 1_500_000):
        self.max_bytes = max_bytes
        self._http = httpx.Client(timeout=timeout, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0 (venture-builder research; +local)"})

    def __call__(self, url: str) -> str:
        try:
            resp = self._http.get(url)
        except httpx.HTTPError:
            return ""
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
            return ""
        return readable_text(resp.text[: self.max_bytes])
