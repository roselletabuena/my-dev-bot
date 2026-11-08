from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")
_SECRET_PREFIXES = ("JIRA_", "GITHUB_", "GH_", "SLACK_")


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Process env without the bot's own credentials, for anything that runs untrusted-influenced code."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(_SECRET_PREFIXES) and not any(m in k.upper() for m in _SECRET_MARKERS)
    }
    env.update(extra or {})
    return env


@dataclass
class EngineResult:
    ok: bool
    response: str = ""
    error: str | None = None
    duration_s: float = 0.0
    raw: dict = field(default_factory=dict)


class CodeEngine(Protocol):
    def run(self, workdir: Path, prompt: str, timeout_s: int) -> EngineResult: ...


def run_cli(
    cmd: list[str], workdir: Path, timeout_s: int, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run an engine CLI headlessly: scrubbed env, hard wall-clock timeout.

    Pass untrusted text (ticket content) via `stdin_text`, not argv: on Windows, npm shims like
    `claude.cmd` run through cmd.exe, which would interpret characters such as & and % in argv.
    """
    exe = shutil.which(cmd[0])  # resolves .cmd/.exe via PATHEXT; plain "claude" is not found otherwise
    if exe is None:
        raise FileNotFoundError(cmd[0])
    return subprocess.run(
        [exe, *cmd[1:]],
        cwd=workdir,
        env=scrubbed_env(),
        input=stdin_text if stdin_text is not None else "",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_s,
    )


def parse_last_json(stdout: str) -> dict | None:
    """The CLIs print a final JSON object; tolerate warnings/noise before it."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None
