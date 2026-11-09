from __future__ import annotations

import json
import re
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


def _parse_readiness_fallback(text: str) -> Readiness | None:
    """Best-effort extraction if the model emitted a formatted text/markdown verdict instead of JSON."""
    m = re.search(
        r"(?:readiness\s+verdict|verdict|status)[^\n:]*:\s*(?:<[^>]+>|[*\s`])*\b(NOT\s*READY|UNREADY|READY)\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    verdict = m.group(1).upper()
    if "NOT" in verdict or "UN" in verdict:
        questions = [
            line.strip().lstrip("-*0123456789.) ").strip()
            for line in text.splitlines()
            if line.strip().startswith(("-", "*")) and "?" in line
        ]
        if not questions:
            questions = ["Please add more detail and acceptance criteria so this can be implemented."]
        return Readiness(ready=False, questions=questions)
    return Readiness(ready=True, questions=[])


def check_readiness(engine: CodeEngine, issue: Issue, timeout_s: int) -> Readiness:
    # Runs in an empty scratch dir: triage needs the ticket text only, never repo access.
    with tempfile.TemporaryDirectory(prefix="dev-triage-", ignore_cleanup_errors=True) as scratch:
        result = engine.run(Path(scratch), build_triage_prompt(issue), timeout_s)
    if not result.ok:
        raise TriageError(f"engine failed during readiness check: {result.error}")
    data = extract_json(result.response)
    if data is not None and "ready" in data:
        questions = [str(q) for q in data.get("questions") or []]
        ready = bool(data["ready"])
        if not ready and not questions:
            questions = ["Please add more detail and acceptance criteria so this can be implemented."]
        return Readiness(ready=ready, questions=[] if ready else questions)

    fallback = _parse_readiness_fallback(result.response)
    if fallback is not None:
        return fallback

    raise TriageError(f"unparseable readiness reply: {result.response[:200]!r}")
