"""What a story ticket promises, read deterministically from its description (no engine call).

Story tickets follow a fixed template: a "Technical Scope" section with `Layer:` and `Target Files /
Directories:`, an "Acceptance Criteria" section of `Scenario N:` blocks (Given/When/Then), and a
"Definition of Done" checklist. Each section is optional: a ticket that lacks one simply yields an empty
list, and the caller decides whether that matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .jira import Issue

# Section headers, matched against a line with its leading emoji/punctuation stripped.
_HEADERS = {
    "user story": "story",
    "context": "context",
    "technical scope": "scope",
    "issue links": "links",
    "acceptance criteria": "ac",
    "quality gate": "dod",
    "definition of done": "dod",
}
_LEADING_NOISE = re.compile(r"^[^A-Za-z0-9\[]+")
_SCENARIO = re.compile(r"^Scenario\s+(\d+)\s*[:.\-–]\s*(.*)$", re.IGNORECASE)
_STEP = re.compile(r"^(Given|When|Then|And|But)\b", re.IGNORECASE)
_CHECKBOX = re.compile(r"^(?:[-*]\s*)?\[[ xX]?\]\s*(.+)$")
_LAYER = re.compile(r"^Layer\s*:\s*(.*)$", re.IGNORECASE)
_TARGETS = re.compile(r"^Target\s+Files", re.IGNORECASE)
_TARGETS_END = re.compile(r"^Architectural\s+Constraints", re.IGNORECASE)
# Repo-relative paths named in prose, e.g. "Layer: ... (src/features/events/, src/features/voting/)".
_ROOTS = ("src/", "prisma/", "docs/", "tests/", "test/", "public/", "scripts/", "infra/", "app/", "lib/", ".github/")
_PROSE_PATH = re.compile(r"(?<![\w/.-])((?:" + "|".join(re.escape(r) for r in _ROOTS) + r")[\w.\-\[\]@/]*)")
_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{1,6}$")


@dataclass
class Scenario:
    number: int
    title: str
    steps: list[str] = field(default_factory=list)

    @property
    def tag(self) -> str:
        return f"S{self.number}"


@dataclass
class StoryContract:
    key: str
    layer: str = ""
    side: str = ""  # backend | frontend | "" when the ticket does not say
    target_paths: list[str] = field(default_factory=list)  # repo-relative files/dirs the story names
    target_notes: list[str] = field(default_factory=list)  # target lines that name no path
    scenarios: list[Scenario] = field(default_factory=list)
    dod: list[str] = field(default_factory=list)

    def ac_tag(self, scenario: Scenario) -> str:
        """The marker a test carries to show it covers `scenario`, e.g. `AC: VS-212 S2`."""
        return f"AC: {self.key} {scenario.tag}"


def _header(line: str) -> str | None:
    text = _LEADING_NOISE.sub("", line).strip().lower()
    if not text or len(text) > 80:
        return None
    return next((kind for prefix, kind in _HEADERS.items() if text.startswith(prefix)), None)


def _target_path(line: str) -> str | None:
    """The path a "Target Files" line starts with, or None for a descriptive line."""
    token = line.split()[0].rstrip(",;:)") if line.split() else ""
    if "/" not in token and not _FILE_EXT.search(token):
        return None
    if token.endswith("/") or _FILE_EXT.search(token) or token.startswith(_ROOTS):
        return token
    return None  # e.g. "Candidate/Contestant creation ...": a slash, but not a path


def _side(issue: Issue, layer: str) -> str:
    labels = {label.lower() for label in issue.labels}
    if "backend" in labels and "frontend" not in labels:
        return "backend"
    if "frontend" in labels and "backend" not in labels:
        return "frontend"
    head = issue.summary.strip().upper()
    if head.startswith("BE:") or head.startswith("BE "):
        return "backend"
    if head.startswith("FE:") or head.startswith("FE "):
        return "frontend"
    lowered = layer.lower()
    if "frontend" in lowered:
        return "frontend"
    if "backend" in lowered or "prisma" in lowered or "api" in lowered:
        return "backend"
    return ""


def parse_story(issue: Issue) -> StoryContract:
    section = ""
    in_targets = False
    layer = ""
    paths: list[str] = []
    notes: list[str] = []
    scenarios: list[Scenario] = []
    dod: list[str] = []

    for raw in issue.description.splitlines():
        line = raw.strip()
        if not line:
            continue
        kind = _header(line)
        if kind:
            section, in_targets = kind, False
            continue

        if section == "scope" or (not section and not scenarios):
            if m := _LAYER.match(line):
                layer = m.group(1).strip()
                paths.extend(p.rstrip(",;:)") for p in _PROSE_PATH.findall(layer))
                continue
            if _TARGETS.match(line):
                in_targets = True
                continue
            if _TARGETS_END.match(line):
                in_targets = False
                continue
            if in_targets:
                path = _target_path(line)
                if path:
                    paths.append(path)
                else:
                    notes.append(line)
                continue

        if m := _SCENARIO.match(line):  # scenarios are recognised in any section, header or not
            scenarios.append(Scenario(int(m.group(1)), m.group(2).strip()))
            continue
        if scenarios and section in ("ac", "") and _STEP.match(line):
            scenarios[-1].steps.append(line)
            continue
        if section == "dod" and (m := _CHECKBOX.match(line)):
            dod.append(m.group(1).strip())

    return StoryContract(
        key=issue.key,
        layer=layer,
        side=_side(issue, layer),
        target_paths=list(dict.fromkeys(paths)),
        target_notes=notes,
        scenarios=scenarios,
        dod=dod,
    )


def paths_overlap(a: str, b: str) -> bool:
    """Same file, or one is a directory (trailing slash) containing the other."""
    if a == b:
        return True
    return (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def shared_paths(a: StoryContract, b: StoryContract) -> list[str]:
    shared = []
    for p in a.target_paths:
        for q in b.target_paths:
            if paths_overlap(p, q):
                shared.append(p if len(p) >= len(q) else q)  # the more specific of the two
    return list(dict.fromkeys(shared))
