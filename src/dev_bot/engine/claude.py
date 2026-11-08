from __future__ import annotations

import subprocess
from pathlib import Path

from .base import EngineResult, parse_last_json, run_cli


class ClaudeEngine:
    """Engine wrapping `claude -p` (Claude Code headless).

    Verified on this machine (claude 2.1.314): `-p`, `--output-format json`, `--permission-mode acceptEdits`
    edits files non-interactively using the existing login. The final JSON has `result`, `is_error`,
    `duration_ms`, `total_cost_usd`.
    """

    def __init__(
        self,
        executable: str = "claude",
        permission_mode: str = "acceptEdits",
        max_turns: int = 40,
        max_budget_usd: float = 3.0,
    ):
        self.executable = executable
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd  # hard per-call cost cap

    def run(self, workdir: Path, prompt: str, timeout_s: int) -> EngineResult:
        cmd = [
            self.executable,
            "-p",  # prompt is read from stdin (see run_cli)
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
            "--max-turns",
            str(self.max_turns),
            "--max-budget-usd",
            str(self.max_budget_usd),
        ]
        try:
            proc = run_cli(cmd, workdir, timeout_s, stdin_text=prompt)
        except subprocess.TimeoutExpired:
            return EngineResult(ok=False, error=f"claude exceeded {timeout_s}s")
        except FileNotFoundError:
            return EngineResult(ok=False, error=f"{self.executable!r} not found on PATH")

        data = parse_last_json(proc.stdout) or {}
        ok = proc.returncode == 0 and not data.get("is_error", False)
        return EngineResult(
            ok=ok,
            response=str(data.get("result", "")),
            error=None if ok else (str(data.get("result") or proc.stderr.strip()[-500:]) or f"claude exited {proc.returncode}"),
            duration_s=float(data.get("duration_ms", 0)) / 1000,
            raw=data,
        )
