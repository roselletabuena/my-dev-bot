"""Plan logical, single-purpose atomic commits for a verified diff.

The engine proposes commit groupings as JSON; the bot validates every file path
against the actual changed-file list and executes the signed git commits itself.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .engine.base import CodeEngine
from .jira import Issue
from .prompt import build_atomic_commit_prompt

log = logging.getLogger("dev_bot")

_MAX_DIFF_CHARS = 40_000


@dataclass(frozen=True)
class CommitGroup:
    message: str
    files: list[str]


def _extract_commits_json(text: str) -> dict | None:
    """Find a JSON object containing 'commits' in free-form model output."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("commits"), list):
                return obj
    return None


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _with_ticket_prefix(issue_key: str, message: str) -> str:
    msg = message.strip().splitlines()[0].strip()
    if msg.upper().startswith(issue_key.upper()):
        return msg
    return f"{issue_key}: {msg}"


def parse_commit_plan(raw: str, issue_key: str, changed_files: list[str]) -> list[CommitGroup] | None:
    """Validate and normalize a model commit plan against `changed_files`.

    Returns None when the output is unparseable or yields no valid commits so the
    caller can fall back to a single commit without blocking the PR.
    """
    payload = _extract_commits_json(raw)
    if payload is None:
        return None

    allowed = {_normalize_path(f): f for f in changed_files}
    seen: set[str] = set()
    groups: list[CommitGroup] = []

    for entry in payload["commits"]:
        if not isinstance(entry, dict):
            continue
        raw_msg = entry.get("message")
        raw_files = entry.get("files")
        if not isinstance(raw_msg, str) or not raw_msg.strip() or not isinstance(raw_files, list):
            continue
        group_files: list[str] = []
        for item in raw_files:
            if not isinstance(item, str):
                continue
            norm = _normalize_path(item)
            if norm in allowed and norm not in seen:
                seen.add(norm)
                group_files.append(allowed[norm])
        if group_files:
            groups.append(CommitGroup(message=_with_ticket_prefix(issue_key, raw_msg), files=group_files))

    if not groups:
        return None

    remaining = [f for norm, f in allowed.items() if norm not in seen]
    if remaining:
        groups.append(CommitGroup(message=f"{issue_key}: chore: remaining changes", files=remaining))

    return groups


def plan_atomic_commits(
    engine: CodeEngine,
    issue: Issue,
    files: list[str],
    diff: str,
    timeout_s: int,
) -> list[CommitGroup] | None:
    """Ask the engine to split `files` into atomic commits, or return None to fall back."""
    if len(files) <= 1:
        return None
    clipped = diff if len(diff) <= _MAX_DIFF_CHARS else diff[:_MAX_DIFF_CHARS] + "\n... [diff truncated]"
    prompt = build_atomic_commit_prompt(issue, files, clipped)
    try:
        with tempfile.TemporaryDirectory(prefix="dev-commits-", ignore_cleanup_errors=True) as scratch:
            result = engine.run(Path(scratch), prompt, timeout_s)
    except Exception:
        log.exception("Atomic commit planning crashed; falling back to single commit")
        return None
    if not result.ok:
        log.warning("Atomic commit planning failed (%s); falling back to single commit", result.error)
        return None
    groups = parse_commit_plan(result.response, issue.key, files)
    if groups is None:
        log.warning("Atomic commit plan was invalid; falling back to single commit")
    return groups
