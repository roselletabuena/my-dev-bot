"""Read-only view of a repo's Spec Kit folders (`specs/NNN-slug/`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+")
_FOLDER = re.compile(r"^(\d{3,})-")
_TASK_OPEN = re.compile(r"^\s*[-*]\s*\[ \]", re.MULTILINE)
_TASK_PREFIX = re.compile(r"^\s*[-*]\s*\[ \]\s*(?:T\d+\s*)?(?:\[[^\]]*\]\s*)*")
_CHECK_COMMAND = re.compile(r"npm run|npx|quality gates?", re.IGNORECASE)


@dataclass
class Spec:
    folder: str  # "015-core-voting-engine"
    title: str
    jira_key: str  # the ticket the spec was written for, "" when unrecorded


def _parse(spec_md: Path, folder: str) -> Spec:
    title, key = folder, ""
    for line in spec_md.read_text(encoding="utf-8", errors="replace").splitlines()[:30]:
        if line.startswith("# "):
            title = line[2:].removeprefix("Feature Specification:").strip() or folder
        elif line.lower().startswith("**jira key**") and not key:
            match = _KEY.search(line)  # first key only: the line may also name the parent epic
            key = match.group(0) if match else ""
    return Spec(folder, title, key)


def list_specs(root: Path, spec_dir: str) -> list[Spec]:
    base = root / spec_dir
    if not base.is_dir():
        return []
    return [
        _parse(d / "spec.md", d.name)
        for d in sorted(base.iterdir())
        if d.is_dir() and _FOLDER.match(d.name) and (d / "spec.md").is_file()
    ]


def next_folder(root: Path, spec_dir: str, slug: str) -> str:
    """`NNN-slug` with NNN one past the highest existing number (folders without a spec.md count too)."""
    base = root / spec_dir
    numbers = [int(m.group(1)) for d in base.iterdir() if (m := _FOLDER.match(d.name))] if base.is_dir() else []
    return f"{max(numbers, default=0) + 1:03d}-{slug}"


def find_by_reference(specs: list[Spec], text: str, keys: list[str]) -> Spec | None:
    """The spec a ticket points at: its folder name quoted in `text`, or a spec recorded for one of `keys`."""
    for spec in specs:
        if spec.folder in text:
            return spec
    for key in filter(None, keys):
        matches = [s for s in specs if s.jira_key == key]
        if len(matches) == 1:  # several specs for one key is ambiguous: leave it to the classifier
            return matches[0]
    return None


def unfinished_tasks(tasks_md: str, tag: str = "") -> list[str]:
    """Unticked checklist lines in tasks.md, optionally only those mentioning `tag` (e.g. a ticket key)."""
    open_lines = [line.strip() for line in tasks_md.splitlines() if _TASK_OPEN.match(line)]
    return [line for line in open_lines if tag in line]


def is_check_task(line: str) -> bool:
    """A task that only runs the repo's checks ("Run quality gates (`npm run lint`, ...)").

    The engine runs headless and cannot execute them, and the pipeline runs `check_commands` itself after
    the stage, so such a task never blocks the implement stage.
    """
    text = _TASK_PREFIX.sub("", line, count=1)
    return text[:4].lower() == "run " and bool(_CHECK_COMMAND.search(text))
