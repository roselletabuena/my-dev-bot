from __future__ import annotations

import subprocess
from pathlib import Path

from .base import EngineResult, parse_last_json, run_cli


class AntigravityEngine:
    """Wraps `agy` in print mode.

    Verified on this machine (agy --help): `-p`, `--output-format json`, `--print-timeout`,
    `--mode accept-edits`, `--sandbox`. Flags must precede `-p`. `--dangerously-skip-permissions`
    is deliberately NOT used. accept-edits is enough because the bot runs setup/checks itself.
    Failure shape seen: exit 3 and {"status": "ERROR", "error": "..."} (e.g. credits exhausted).
    """

    def __init__(self, executable: str = "agy", mode: str = "accept-edits", sandbox: bool = True):
        self.executable = executable
        self.mode = mode
        self.sandbox = sandbox

    def run(self, workdir: Path, prompt: str, timeout_s: int) -> EngineResult:
        cmd = [self.executable, "--mode", self.mode, "--print-timeout", f"{timeout_s}s", "--output-format", "json"]
        if self.sandbox:
            cmd.append("--sandbox")
        cmd += ["-p", prompt]

        try:
            proc = run_cli(cmd, workdir, timeout_s + 30)
        except subprocess.TimeoutExpired:
            return EngineResult(ok=False, error=f"agy exceeded {timeout_s}s")
        except FileNotFoundError:
            return EngineResult(ok=False, error=f"{self.executable!r} not found on PATH")

        data = parse_last_json(proc.stdout) or {}
        status = str(data.get("status", "")).upper()
        error = data.get("error") or (proc.stderr.strip()[-500:] if proc.returncode else None)
        ok = proc.returncode == 0 and status != "ERROR" and not data.get("error")
        return EngineResult(
            ok=ok,
            response=str(data.get("response", "")),
            error=None if ok else (error or f"agy exited {proc.returncode}"),
            duration_s=float(data.get("duration_seconds", 0.0)),
            raw=data,
        )
