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
    """Search, read the top `fetch_pages` pages in full (quoting the passages that match the query), and
    write every quote to the ledger. Returns the evidence ids."""
    """Return evidence IDs for one query. Raises SearchUnavailable if search is down."""
    results = search(query, limit=max_sources + 3)
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


import re

# A price quote counts only if it names a product a buyer could compare with ours, so "$1,500 for an app design
# project" or "a $5,000 loan" never sets the price range.
_PRICE = re.compile(r"\$\s?\d")
_PRODUCT = re.compile(r"\b(templates?|guides?|kits?|packs?|bundles?|checklists?|e-?books?|courses?|toolkits?|"
                      r"workbooks?|planners?|downloads?|notion|canva|spreadsheets?|worksheets?)\b", re.I)
_NOT_A_PRODUCT_PRICE = re.compile(r"per hour|/ ?h(ou)?r\b|hourly|salary|loan|invoice (of|for)|budget of|project cost",
                                  re.I)


def price_quotes(text: str) -> list[str]:
    """Sentences that state a price for a comparable digital product."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = sentence.strip()
        if 10 <= len(s) <= 300 and _PRICE.search(s) and _PRODUCT.search(s) and not _NOT_A_PRODUCT_PRICE.search(s):
            out.append(s)
    return out


def research_prices(queries: list[str], *, ledger: EvidenceLedger, search, fetch, fetch_pages: int = 2,
                    agent: str = "price_research") -> list[str]:
    """Search for what comparable products cost; write each price sentence to the ledger (tools only).
    Returns the evidence ids; an empty list if search is unavailable."""
    ids: list[str] = []
    for query in queries:
        try:
            results = search(query, limit=8)
        except SearchUnavailable:
            log_action(agent, "web_search_failed", query=query)
            continue
        found: list[str] = []
        for r in results:
            for quote in price_quotes(r.get("content", "")):
                found.append(ledger.add(tool="web_search", type="web", url=r["url"], snippet=quote, query=query).id)
        for r in results[:fetch_pages]:
            for quote in price_quotes(fetch(r["url"]) or "")[:3]:
                found.append(ledger.add(tool="fetch_page", type="web", url=r["url"], snippet=quote, query=query).id)
        log_action(agent, "web_search", sources=[r["url"] for r in results], query=query, evidence_ids=found)
        ids += found
    return list(dict.fromkeys(ids))


__all__ = ["research", "research_prices", "price_quotes", "SearchUnavailable"]
