"""The repo's own agent skills, surfaced to the coding engine.

A repo that keeps skills (`.agents/skills/<name>/SKILL.md`, mirrored to `.claude/skills` for Claude Code)
has written down how its code should be built. The engine runs headless and chooses nothing on its own,
so the bot lists the relevant skills in the prompt, with the path of each, and tells the engine to read
the ones that apply before it writes code. That works for any engine that can read files.

Skills come from the worktree, which is cut from `origin/<default branch>`, so only reviewed content
reaches the prompt. Discovery never raises: a malformed skill is skipped.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import RepoConfig

log = logging.getLogger("dev_bot")

DESCRIPTION_MAX = 200
_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_BLOCK_SCALAR = re.compile(r"^[>|][+-]?\s*")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str  # first ~200 characters, one line
    path: str  # repo-relative, forward slashes: ".agents/skills/<name>/SKILL.md"


@dataclass
class Guidance:
    """What the repo itself says about how to build in it, as the prompts need it."""

    skills: list[Skill] = field(default_factory=list)  # offered to the engine (include/exclude/max applied)
    read_first: list[str] = field(default_factory=list)  # existing files the engine must read first
    known: list[Skill] = field(default_factory=list)  # every skill found, for lookups by name
    bugfix_skill: Skill | None = None
    today: str = field(default_factory=lambda: date.today().isoformat())  # skills ask for dates; a headless engine may not know

    def skill_file(self, name: str) -> str:
        """Path of skill `name`, wherever the repo keeps it; the conventional location if it is not found."""
        found = next((s for s in self.known if s.name == name), None)
        return found.path if found else f".agents/skills/{name}/SKILL.md"


def parse_frontmatter(text: str) -> dict[str, str]:
    """Top-level `key: value` pairs of a SKILL.md header; folded or indented continuations are joined.

    A deliberately small reader (no YAML dependency): skills only need `name` and `description`.
    """
    block = _FRONTMATTER.match(text)
    if not block:
        return {}
    meta: dict[str, str] = {}
    key = ""
    for line in block.group(1).splitlines():
        top = _TOP_LEVEL_KEY.match(line)
        if top:
            key = top.group(1)
            meta[key] = top.group(2).strip()
        elif key and line.strip() and line[:1] in (" ", "\t"):
            meta[key] = f"{meta[key]} {line.strip()}".strip()
    return {k: _clean(v) for k, v in meta.items()}


def _clean(value: str) -> str:
    value = _BLOCK_SCALAR.sub("", value).strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        value = value[1:-1].replace("''", "'")  # YAML escapes a quote inside single quotes by doubling it
    elif len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"')
    return " ".join(value.split())


def _shorten(text: str, limit: int = DESCRIPTION_MAX) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,;:-") + "..."


def discover_skills(worktree: Path, repo: RepoConfig) -> list[Skill]:
    """Every readable skill under `repo.skills_dirs`, sorted by name; the first directory wins a name clash."""
    found: dict[str, Skill] = {}
    for directory in repo.skills_dirs:
        root = worktree / directory
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                meta = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
                path = skill_md.relative_to(worktree).as_posix()
            except (OSError, ValueError):
                log.debug("skipping unreadable skill %s", skill_md)
                continue
            name = meta.get("name") or skill_md.parent.name
            found.setdefault(name, Skill(name, _shorten(meta.get("description", "")), path))
    return sorted(found.values(), key=lambda s: s.name)


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def select_skills(skills: list[Skill], repo: RepoConfig) -> list[Skill]:
    """The skills worth offering for this repo: `skills_include` (if set), minus `skills_exclude`, capped."""
    chosen = [
        s
        for s in skills
        if (not repo.skills_include or _matches_any(s.name, repo.skills_include))
        and not _matches_any(s.name, repo.skills_exclude)
    ]
    if len(chosen) > repo.skills_max:
        log.warning(
            "%s: %d skills match but only %d are offered (skills_max); narrow skills_include/skills_exclude",
            repo.name, len(chosen), repo.skills_max,
        )
    return chosen[: repo.skills_max]


def gather_guidance(worktree: Path, repo: RepoConfig) -> Guidance:
    known = discover_skills(worktree, repo)
    read_first = []
    for rel in repo.read_first:
        if (worktree / rel).is_file():
            read_first.append(rel.replace("\\", "/"))
        else:  # telling the engine to read a file that is not there only invites it to invent one
            log.warning("%s: read_first file %s does not exist on %s; skipped", repo.name, rel, repo.default_branch)
    bugfix_skill = next((s for s in known if s.name == repo.bugfix_skill), None) if repo.bugfix_skill else None
    if repo.bugfix_skill and bugfix_skill is None:
        log.warning("%s: bugfix_skill %r not found under %s", repo.name, repo.bugfix_skill, ", ".join(repo.skills_dirs))
    return Guidance(select_skills(known, repo), read_first, known, bugfix_skill)
