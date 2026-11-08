from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .engine.base import CodeEngine
from .jira import Issue
from .prompt import build_triage_prompt


class TriageError(RuntimeError):
    pass


@dataclass
class Readiness:
    ready: bool
    questions: list[str] = field(default_factory=list)


def extract_json(text: str) -> dict | None:
    """Find the first JSON object in free-form model output."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def check_readiness(engine: CodeEngine, issue: Issue, timeout_s: int) -> Readiness:
    # Runs in an empty scratch dir: triage needs the ticket text only, never repo access.
    with tempfile.TemporaryDirectory(prefix="dev-triage-") as scratch:
        result = engine.run(Path(scratch), build_triage_prompt(issue), timeout_s)
    if not result.ok:
        raise TriageError(f"engine failed during readiness check: {result.error}")
    data = extract_json(result.response)
    if data is None or "ready" not in data:
        raise TriageError(f"unparseable readiness reply: {result.response[:200]!r}")
    questions = [str(q) for q in data.get("questions") or []]
    ready = bool(data["ready"])
    if not ready and not questions:
        questions = ["Please add more detail and acceptance criteria so this can be implemented."]
    return Readiness(ready=ready, questions=[] if ready else questions)
