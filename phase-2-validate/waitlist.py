"""Waitlist storage: CSV files in a git-ignored runtime folder. Emails are personal data and are never committed."""

from __future__ import annotations

import csv
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s,;<>\"']{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Waitlist:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.signups = self.folder / "waitlist.csv"
        self.visits = self.folder / "visits.csv"

    def _append(self, path: Path, row: list[str]):
        with _lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            with path.open("a", newline="") as f:
                csv.writer(f).writerow(row)

    def record_visit(self):
        self._append(self.visits, [_now()])

    def record_signup(self, email: str) -> bool:
        """Store a valid email once. Returns True only for a new, valid sign-up."""
        email = email.strip().lower()
        if not EMAIL_RE.match(email) or email in self._emails():
            return False
        self._append(self.signups, [_now(), email])
        return True

    def _emails(self) -> set[str]:
        if not self.signups.exists():
            return set()
        with self.signups.open(newline="") as f:
            return {row[1] for row in csv.reader(f) if len(row) > 1}

    def counts(self) -> dict:
        visitors = 0
        if self.visits.exists():
            with self.visits.open(newline="") as f:
                visitors = sum(1 for _ in csv.reader(f))
        return {"visitors": visitors, "signups": len(self._emails())}
