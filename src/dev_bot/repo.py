"""Cached clone per target repo + a disposable git worktree per ticket."""

from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import RepoConfig


class GitError(RuntimeError):
    pass


@dataclass
class Changes:
    files: list[str]
    lines: int  # added + deleted


def slugify(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len].strip("-") or "change"


def _rmtree(path: Path) -> None:
    def onerror(func, p, _exc):  # read-only files (e.g. .git objects, node_modules) on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=onerror)


class RepoManager:
    def __init__(
        self,
        root: Path,
        token: str | None = None,
        remote_url: Callable[[str], str] | None = None,
        user_name: str = "dev-bot",
        user_email: str = "dev-bot@users.noreply.github.com",
    ):
        self.root = root
        self.token = token
        self.remote_url = remote_url or (lambda name: f"https://github.com/{name}.git")
        self.user_name = user_name
        self.user_email = user_email

    # -- paths ---------------------------------------------------------------------------------
    def cache_path(self, repo: RepoConfig) -> Path:
        return self.root / "repos" / repo.name.replace("/", "__")

    @property
    def worktrees_dir(self) -> Path:
        return self.root / "worktrees"

    # -- git plumbing --------------------------------------------------------------------------
    def _git(self, *args: str, cwd: Path | None = None, auth: bool = False, check: bool = True) -> str:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if auth and self.token:
            # Token goes via env, never argv or the stored remote URL.
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.extraHeader",
                GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}",
            )
        proc = subprocess.run(
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        if check and proc.returncode:
            raise GitError(f"git {args[0]} failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
        return proc.stdout

    # -- cache + worktrees ---------------------------------------------------------------------
    def ensure_cache(self, repo: RepoConfig) -> Path:
        cache = self.cache_path(repo)
        if not (cache / ".git").exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            self._git("clone", "--no-checkout", self.remote_url(repo.name), str(cache), auth=True)
        else:
            self._git("fetch", "origin", "--prune", cwd=cache, auth=True)
        # Check files out exactly as stored. Windows git defaults to autocrlf=true, which turns an
        # LF repo into CRLF and makes Prettier's format:check fail on every file. Worktrees share
        # this config, and setting it on an existing cache fixes older clones too.
        self._git("config", "core.autocrlf", "false", cwd=cache)
        return cache

    def create_worktree(self, repo: RepoConfig, branch: str) -> Path:
        if not branch.startswith("bot/"):
            raise GitError(f"refusing non-bot branch {branch!r}")
        cache = self.ensure_cache(repo)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        path = self.worktrees_dir / f"{slugify(branch)}-{int(time.time())}"
        self._git("worktree", "add", "-B", branch, str(path), f"origin/{repo.default_branch}", cwd=cache)
        return path

    def remove_worktree(self, repo: RepoConfig, path: Path) -> None:
        cache = self.cache_path(repo)
        self._git("worktree", "remove", "--force", str(path), cwd=cache, check=False)
        if path.exists():
            _rmtree(path)
        self._git("worktree", "prune", cwd=cache, check=False)

    def prune_stale(self) -> None:
        """Call at startup (under the run lock): remove worktrees leaked by crashed runs."""
        if self.worktrees_dir.exists():
            for leftover in self.worktrees_dir.iterdir():
                if leftover.is_dir():
                    _rmtree(leftover)
        repos = self.root / "repos"
        if repos.exists():
            for cache in repos.iterdir():
                if (cache / ".git").exists():
                    self._git("worktree", "prune", cwd=cache, check=False)

    # -- inspecting and publishing changes -----------------------------------------------------
    def head_sha(self, worktree: Path) -> str:
        """The commit a fresh worktree starts from; the fixed base for everything the engine does."""
        return self._git("rev-parse", "HEAD", cwd=worktree).strip()

    def collect_changes(self, worktree: Path, base: str) -> Changes:
        """Stage everything and report what differs from `base`.

        Measured against the starting commit, not HEAD, so it stays correct even if the engine
        committed on its own despite being told not to.
        """
        self._git("add", "-A", cwd=worktree)
        files = [f for f in self._git("diff", "--cached", "--name-only", base, cwd=worktree).splitlines() if f]
        lines = 0
        for row in self._git("diff", "--cached", "--numstat", base, cwd=worktree).splitlines():
            added, deleted, *_ = row.split("\t")
            lines += (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
        return Changes(files=files, lines=lines)

    def diff_text(self, worktree: Path, base: str) -> str:
        """Full staged diff against `base` (call after collect_changes, which stages everything)."""
        return self._git("diff", "--cached", base, cwd=worktree)

    def commit(self, worktree: Path, message: str, base: str) -> None:
        """One bot-authored commit on top of `base` (squashing any commits the engine made).

        Normal commit: repo hooks (e.g. Husky lint-staged) run. Never --no-verify.
        """
        self._git("reset", "--soft", base, cwd=worktree)
        self._git(
            "-c", f"user.name={self.user_name}", "-c", f"user.email={self.user_email}",
            "commit", "-m", message,
            cwd=worktree,
        )

    def push(self, worktree: Path, branch: str) -> None:
        if not branch.startswith("bot/"):
            raise GitError(f"refusing to push non-bot branch {branch!r}")
        # The bot owns bot/* branches, so a retried ticket may overwrite its earlier attempt.
        self._git("push", "--force", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree, auth=True)
