"""Independent checks on what the engine produced. Never trust the agent's own claims."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import RepoConfig
from .engine.base import scrubbed_env
from .repo import Changes


@dataclass
class CheckResult:
    command: str
    ok: bool
    output_tail: str


def _matches(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)


def check_changes(changes: Changes, repo: RepoConfig) -> list[str]:
    """Return human-readable violations; empty list means the diff is acceptable."""
    if not changes.files:
        return ["The agent made no changes."]
    problems: list[str] = []
    if changes.lines > repo.max_diff_lines:
        problems.append(f"Diff is {changes.lines} lines; the limit is {repo.max_diff_lines}.")
    for f in changes.files:
        if any(_matches(f, p) for p in repo.denied_paths):
            problems.append(f"Touches protected path: {f}")
        elif repo.allowed_paths and not any(_matches(f, p) for p in repo.allowed_paths):
            problems.append(f"Outside allowed paths: {f}")
    return problems


def run_commands(
    commands: list[str], cwd: Path, timeout_s: int = 1200, extra_env: dict[str, str] | None = None
) -> list[CheckResult]:
    """Run commands in order, stopping at the first failure. Commands come from trusted config."""
    env = scrubbed_env({"CI": "1", **(extra_env or {})})
    results: list[CheckResult] = []
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, env=env, shell=True, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=timeout_s,
            )
            ok, out = proc.returncode == 0, (proc.stdout + proc.stderr)
        except subprocess.TimeoutExpired:
            ok, out = False, f"timed out after {timeout_s}s"
        results.append(CheckResult(cmd, ok, out.strip()[-4000:]))  # enough for a repair prompt
        if not ok:
            break
    return results
