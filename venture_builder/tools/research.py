"""Research tool used by the researchers: search, fetch, and `ledger.add`.

Agents choose the queries; this code runs them and writes evidence. The agent then sees only
evidence IDs with quotes and cites IDs; it never writes a URL itself.
"""

from __future__ import annotations

from ..decision_log import log_action
from ..ledger import EvidenceLedger
from .search import SearchUnavailable


def research(query: str, *, ledger: EvidenceLedger, search, fetch, max_sources: int = 5,
             fetch_pages: int = 2, agent: str = "research_tool") -> list[str]:
    """Return evidence IDs for one query. Raises SearchUnavailable if search is down."""
    results = search(query, limit=max_sources)
    ids: list[str] = []
    for r in results[:max_sources]:
        if r.get("content"):
            ids.append(ledger.add(tool="web_search", type="web", url=r["url"], snippet=r["content"], query=query).id)
    for r in results[:fetch_pages]:
        text = fetch(r["url"])
        if not text:
            continue
        from .fetch import snippets
        for quote in snippets(text, query, k=2):
            ids.append(ledger.add(tool="fetch_page", type="web", url=r["url"], snippet=quote, query=query).id)
    log_action(agent, "web_search", sources=[r["url"] for r in results], query=query, evidence_ids=ids)
    return list(dict.fromkeys(ids))


__all__ = ["research", "SearchUnavailable"]
