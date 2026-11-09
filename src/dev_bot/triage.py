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
    """Find a JSON object in free-form model output, prioritizing one containing 'ready'."""
    decoder = json.JSONDecoder()
    first_dict: dict | None = None
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                if "ready" in obj:
                    return obj
                if first_dict is None:
                    first_dict = obj
    return first_dict


_NOT_READY_RE = re.compile(
    r"\b(NOT\s*READY|UNREADY|NOT_READY|NEEDS\s*INFO(?:RMATION)?|NEEDS\s*CLARIFICATION|NEEDS\s*MORE\s*INFO|INSUFFICIENT\s*(?:INFO|DETAIL)|NOT\s*ENOUGH\s*(?:INFO|DETAIL)|INCOMPLETE|BLOCKED|FAIL(?:ED)?)\b",
    re.IGNORECASE,
)
_READY_RE = re.compile(r"\b(READY|PASSED|PASS)\b", re.IGNORECASE)


def _extract_questions(text: str) -> list[str]:
    """Extract question items or missing details from fallback markdown text."""
    section_match = re.search(
        r"(?:###?\s*(?:\d+[.)]\s*)?)?(?:missing\s+information|questions|clarifications|ambiguities|blockers|missing\s+details)[^\n:]*[:\n]"
        r"([\s\S]*?)(?=(?:\n###?|\Z))",
        text,
        re.IGNORECASE,
    )
    extracted: list[str] = []
    if section_match:
        section_text = section_match.group(1).strip()
        for line in section_text.splitlines():
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^(?:[-*•]|\d+[.)]|\[[\s xX]?\]|Question\s*\d*[:.-]?)\s*", "", line).strip()
            if cleaned and not cleaned.lower().startswith("none") and len(cleaned) > 5:
                extracted.append(cleaned)

    if not extracted:
        for line in text.splitlines():
            line = line.strip()
            if re.match(r"^(?:[-*•]|\d+[.)]|Question\s*\d*[:.-]?)\s*", line, re.IGNORECASE) and "?" in line:
                cleaned = re.sub(r"^(?:[-*•]|\d+[.)]|Question\s*\d*[:.-]?)\s*", "", line).strip()
                if cleaned:
                    extracted.append(cleaned)

    seen = set()
    unique: list[str] = []
    for q in extracted:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    if not unique:
        unique = ["Please add more detail and acceptance criteria so this can be implemented."]
    return unique


def _parse_readiness_fallback(text: str) -> Readiness | None:
    """Best-effort extraction if the model emitted a formatted text/markdown verdict instead of JSON."""
    # 1. Section/label search (e.g. "### Readiness Verdict\nREADY", "Verdict: NOT READY", etc.)
    for m in re.finditer(
        r"(?:^|[#\n*|])\s*(?:\d+[.)]\s*)?"
        r"(?:"
        r"readiness\s+(?:verdict|status|assessment|decision|check\s+result|check)"
        r"|final\s+verdict"
        r"|verdict(?:\s+(?:and|&)\s+recommendation)?"
        r"|ticket\s+readiness"
        r"|readiness"
        r"|verdict"
        r"|status"
        r"|assessment"
        r"|decision"
        r"|conclusion"
        r"|recommendation"
        r"|ready"
        r")"
        r"(?!\s*check:\s*\[?[A-Z]+-\d+)"  # skip document header like '# Readiness Check: [AD-4]'
        r"[\s\*:`|\#-]*"
        r"([^\n#]+(?:\n(?![#\n])[^\n#]+)*)",
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        block = m.group(1).strip()
        if _NOT_READY_RE.search(block):
            return Readiness(ready=False, questions=_extract_questions(text))
        if _READY_RE.search(block):
            return Readiness(ready=True, questions=[])
        if re.search(r"\b(false|no)\b", block, re.IGNORECASE):
            return Readiness(ready=False, questions=_extract_questions(text))
        if re.search(r"\b(true|yes)\b", block, re.IGNORECASE):
            return Readiness(ready=True, questions=[])

    # 2. Inline/direct mention fallback
    m_direct = re.search(
        r"(?:readiness|verdict|status|ticket\s+is|is|conclusion)[\s\*:`|\#-]*"
        r"(?:the\s+ticket\s+is\s+|ticket\s+is\s+|currently\s+)?"
        r"\b(NOT\s*READY|UNREADY|NOT_READY|NEEDS\s*INFO(?:RMATION)?|NEEDS\s*CLARIFICATION|INCOMPLETE|BLOCKED|READY)\b",
        text,
        re.IGNORECASE,
    )
    if m_direct:
        verdict = m_direct.group(1).upper()
        if _NOT_READY_RE.search(verdict):
            return Readiness(ready=False, questions=_extract_questions(text))
        return Readiness(ready=True, questions=[])

    return None


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
