"""Memory across projects: which websites have yielded usable evidence before.

Only site names and counts are kept (never queries, ideas or quotes), so one founder's idea never leaks into
another project. The researchers get the top sites as hints for `site:` searches."""

from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.Lock()
GENERIC = {"youtube.com", "google.com", "facebook.com", "wikipedia.org", "en.wikipedia.org"}


def _path(runs_dir: Path) -> Path:
    return Path(runs_dir) / ".research_sites.json"


def record(runs_dir: Path, domains: list[str]) -> None:
    domains = [d for d in domains if d]
    if not domains:
        return
    with _LOCK:
        p = _path(runs_dir)
        try:
            data = json.loads(p.read_text()) if p.exists() else {}
        except ValueError:
            data = {}
        for d in domains:
            data[d] = int(data.get(d, 0)) + 1
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sorted(data.items(), key=lambda kv: -kv[1])[:300]), indent=1))
        tmp.replace(p)


def hints(runs_dir: Path, n: int = 8) -> list[str]:
    p = _path(runs_dir)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return []
    return [d for d, _ in sorted(data.items(), key=lambda kv: -kv[1]) if d not in GENERIC][:n]
