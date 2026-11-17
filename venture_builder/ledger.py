"""Evidence Ledger: only tools add evidence; agents cite evidence IDs; code turns IDs into URLs.

Checks applied to every agent claim:
- a citation of an unknown evidence ID rejects the agent output (`CitationError`);
- every number in a claim must appear word for word in a cited snippet, otherwise the claim is
  relabelled `ASSUMPTION`;
- only `web` evidence supports a market claim; `simulated` and `assumption` items weigh 0 everywhere,
  `founder_reported` items confirm the founder's own facts but never market demand.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from .decision_log import now_iso
from .schemas import Claim, EvidenceItem

NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?\s?(?:%|k\b|K\b|m\b|M\b|bn\b)?")

GATE_WEIGHT = {"web": 1.0, "founder_reported": 0.0, "mocked": 0.0, "simulated": 0.0, "assumption": 0.0}


class CitationError(ValueError):
    """An agent cited an evidence ID that is not in the ledger."""


def _norm_number(s: str) -> str:
    return re.sub(r"[\s,$]", "", s).lower()


def numbers_in(text: str) -> list[str]:
    return [m.group(0).strip() for m in NUMBER.finditer(text)]


class EvidenceLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.items: dict[str, EvidenceItem] = {}
        if self.path and self.path.exists():
            for raw in json.loads(self.path.read_text() or "[]"):
                item = EvidenceItem.model_validate(raw)
                self.items[item.id] = item

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([i.model_dump() for i in self.items.values()], indent=1))

    def add(self, *, tool: str, type: str, snippet: str, url: str | None = None, query: str | None = None) -> EvidenceItem:
        """Called by tools only (search, fetch, founder input forms, mocked event entry)."""
        with self._lock:
            for item in self.items.values():  # same source, same quote: reuse the id
                if item.url == url and item.snippet == snippet and item.type == type:
                    return item
            item = EvidenceItem(
                id=f"ev_{len(self.items) + 1}", type=type, url=url,
                domain=(urlparse(url).netloc.removeprefix("www.") if url else None),
                snippet=snippet.strip()[:600], fetched_at=now_iso(), query=query, tool=tool,
            )
            self.items[item.id] = item
            self._save()
            return item

    def get(self, eid: str) -> EvidenceItem | None:
        return self.items.get(eid)

    def unknown(self, ids: list[str]) -> list[str]:
        return [i for i in ids if i not in self.items]

    def require_known(self, ids: list[str]) -> None:
        missing = self.unknown(ids)
        if missing:
            raise CitationError(f"unknown evidence ids: {', '.join(missing)}")

    def check_claim(self, text: str, ids: list[str]) -> Claim:
        """Label a claim SOURCED only if it cites web evidence and every number appears in a cited snippet."""
        self.require_known(ids)
        web = [self.items[i] for i in ids if self.items[i].type == "web"]
        if not web:
            return Claim(text=text, evidence_ids=ids, label="ASSUMPTION")
        snippets = " ".join(_norm_number(i.snippet) for i in web)
        for num in numbers_in(text):
            if _norm_number(num) not in snippets:
                return Claim(text=text, evidence_ids=ids, label="ASSUMPTION")
        return Claim(text=text, evidence_ids=ids, label="SOURCED", sources=sorted({i.url for i in web if i.url}))

    def urls(self, ids: list[str]) -> list[str]:
        return sorted({self.items[i].url for i in ids if i in self.items and self.items[i].url})

    def gate_weight(self, eid: str) -> float:
        item = self.items.get(eid)
        return GATE_WEIGHT.get(item.type, 0.0) if item else 0.0

    def listing(self, ids: list[str] | None = None, max_chars: int = 300) -> list[dict]:
        """Compact view for prompts: id, domain and quote."""
        chosen = [self.items[i] for i in ids] if ids else list(self.items.values())
        return [{"id": i.id, "type": i.type, "domain": i.domain, "quote": i.snippet[:max_chars]} for i in chosen]
