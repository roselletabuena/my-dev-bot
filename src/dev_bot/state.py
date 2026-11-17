"""Local run log. Jira labels are the source of truth for claiming; this is for caps and history."""

from __future__ import annotations

import json
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
        # The engine's review of an Epic (shared contracts), kept so every later run of that Epic builds
        # its stories against the same names instead of a fresh, slightly different review.
        self.db.execute("CREATE TABLE IF NOT EXISTS epic_plans (epic TEXT PRIMARY KEY, verdict TEXT, at TEXT)")
        self.db.commit()

    def save_epic_plan(self, epic: str, verdict: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO epic_plans (epic, verdict, at) VALUES (?, ?, ?)",
            (epic, json.dumps(verdict), datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def load_epic_plan(self, epic: str) -> dict | None:
        row = self.db.execute("SELECT verdict FROM epic_plans WHERE epic = ?", (epic,)).fetchone()
        return json.loads(row[0]) if row else None

    def forget_epic_plan(self, epic: str) -> None:
        self.db.execute("DELETE FROM epic_plans WHERE epic = ?", (epic,))
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
