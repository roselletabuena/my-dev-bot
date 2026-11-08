"""Local run log. Jira labels are the source of truth for claiming; this is for caps and history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            " id INTEGER PRIMARY KEY, ticket TEXT, status TEXT, detail TEXT, pr_url TEXT, at TEXT)"
        )
        self.db.commit()

    def record(self, ticket: str, status: str, detail: str = "", pr_url: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO runs (ticket, status, detail, pr_url, at) VALUES (?, ?, ?, ?, ?)",
            (ticket, status, detail, pr_url, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def count_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.db.execute("SELECT COUNT(*) FROM runs WHERE at >= ?", (today,)).fetchone()
        return int(row[0])

    def close(self) -> None:
        self.db.close()
