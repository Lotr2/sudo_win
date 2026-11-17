"""Free, keyless research sources (read-only GET) and the URL provenance check.

A claim counts as sourced only if its URL was actually fetched in this run (SourceRegistry).
Anything else is relabelled ASSUMPTION. Offline mode reads test fixtures (example.org URLs) and is
used only with the fake LLM.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.conventions import ASSUMPTION

USER_AGENT = "sudo_win-research/0.1 (open-source hackathon prototype)"
FIXTURES = Path(__file__).parent / "tests" / "fixtures"


@dataclass
class Doc:
    url: str
    title: str
    snippet: str


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def search_hn(query: str, n: int = 5) -> list[Doc]:
    q = urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": n})
    data = _get_json(f"https://hn.algolia.com/api/v1/search?{q}")
    docs = []
    for h in data.get("hits", []):
        text = (h.get("story_text") or "")[:300]
        docs.append(Doc(f"https://news.ycombinator.com/item?id={h['objectID']}", h.get("title") or "", text))
    return docs


def search_wikipedia(query: str, n: int = 3) -> list[Doc]:
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n})
    data = _get_json(f"https://en.wikipedia.org/w/api.php?{q}")
    docs = []
    for h in data.get("query", {}).get("search", []):
        title = h["title"]
        snippet = h.get("snippet", "").replace('<span class="searchmatch">', "").replace("</span>", "")
        docs.append(Doc("https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")), title, snippet))
    return docs


class SourceRegistry:
    """Everything fetched in one run. Provenance = membership in this registry."""

    def __init__(self, offline: bool = False, idea: str = "generic"):
        self.offline = offline
        self.idea = idea
        self.docs: dict[str, Doc] = {}
        self.errors: list[str] = []

    def fetch(self, query: str) -> list[Doc]:
        if self.offline:
            path = FIXTURES / f"sources_{self.idea}.json"
            if not path.exists():
                path = FIXTURES / "sources_generic.json"
            docs = [Doc(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
        else:
            docs = []
            for fn in (search_hn, search_wikipedia):
                try:
                    docs += fn(query)
                except Exception as exc:  # a dead source lowers evidence; it never invents any
                    self.errors.append(f"{fn.__name__}: {exc}")
        for d in docs:
            self.docs[d.url] = d
        return docs

    def is_verified(self, url: str | None) -> bool:
        return bool(url) and url in self.docs


def check_provenance(claims: list[dict], registry: SourceRegistry) -> tuple[list[dict], int, int]:
    """Return claims with unverifiable sources relabelled ASSUMPTION, plus (verified, total)."""
    checked, verified = [], 0
    for c in claims or []:
        url = c.get("source_url")
        ok = registry.is_verified(url)
        verified += ok
        checked.append({"text": c.get("text", ""), "source_url": url if ok else None,
                        "label": "sourced" if ok else ASSUMPTION,
                        "dropped_url": url if (url and not ok) else None})
    return checked, verified, len(checked)
