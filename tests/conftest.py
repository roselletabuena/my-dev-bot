from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_bot.config import Config, JiraConfig, RepoConfig
from dev_bot.engine.base import EngineResult
from dev_bot.github import PullRequest
from dev_bot.jira import Issue
from dev_bot.notify import Notifier
from dev_bot.pipeline import Pipeline
from dev_bot.repo import RepoManager
from dev_bot.state import State

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]

TEMPLATE = """## 📌 Jira Ticket & Epic Links

- **Parent Epic**: [VS-XX: Epic Name](https://x.example/browse/VS-XX)

---

## 🎯 Executive Summary

<!-- Concise summary of the objective. -->

---

## 🧪 Testing & Verification Report

- [ ] **Check**: `python -c "print(1)"`
- [ ] **Not run by the bot**: `npm run something-else`

```text
<!-- Paste terminal test output here -->
```

---

## 🏛️ Constitution Compliance Checklist

- [ ] **§I**: Strict type safety.
"""

FILLED = TEMPLATE.replace("[VS-XX: Epic Name](https://x.example/browse/VS-XX)", "None").replace(
    "<!-- Concise summary of the objective. -->", "Changes the greeting for ELE-1 to say hello electa."
)


TASK_ROUTE = '{"workflow": "task", "spec": null, "reason": "small change"}'

SEED_SPEC = "# Feature Specification: Greeting\n\n**Jira Key**: [ELE-9](https://x.example/browse/ELE-9)\n"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def write_skill(root: Path, directory: str, name: str, header: str | None = None) -> None:
    """A skill folder as a repo keeps it (`<directory>/<name>/SKILL.md`, with a frontmatter header)."""
    folder = root / directory / name
    folder.mkdir(parents=True, exist_ok=True)
    header = f"name: {name}\ndescription: How to do {name}." if header is None else header
    (folder / "SKILL.md").write_text(f"---\n{header}\n---\n# {name}\n", encoding="utf-8")


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare 'GitHub' with one commit on main containing src/app.txt and AGENTS.md."""
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("init", "-b", "main", str(seed), cwd=tmp_path)
    (seed / "src").mkdir()
    (seed / "src" / "app.txt").write_text("hello\n")
    (seed / "AGENTS.md").write_text("Be careful.\n")
    (seed / "specs" / "001-greeting").mkdir(parents=True)
    (seed / "specs" / "001-greeting" / "spec.md").write_text(SEED_SPEC)
    (seed / "specs" / "001-greeting" / "tasks.md").write_text("- [x] T001 Say hello in src/app.txt\n")
    (seed / ".github").mkdir()
    (seed / ".github" / "pull_request_template.md").write_text(TEMPLATE, encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_ID, "commit", "-m", "init", cwd=seed)
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    return bare


@pytest.fixture
def repo_cfg() -> RepoConfig:
    return RepoConfig(
        name="acme/app",
        jira_projects=["ELE"],
        check_commands=['python -c "print(1)"'],
        allowed_paths=["src/"],
        denied_paths=[".github/", ".env*"],
        max_diff_lines=50,
    )


@pytest.fixture
def cfg(tmp_path: Path, repo_cfg: RepoConfig) -> Config:
    return Config(
        jira=JiraConfig(base_url="https://x.atlassian.net", jql="project = VS"),
        repos=[repo_cfg],
        work_dir=tmp_path / "work",
    )


class FakeJira:
    def __init__(self):
        self.calls: list[tuple] = []

    def add_label(self, key, label): self.calls.append(("add_label", key, label))
    def remove_label(self, key, label): self.calls.append(("remove_label", key, label))
    def comment(self, key, text): self.calls.append(("comment", key, text))
    def transition(self, key, name):
        self.calls.append(("transition", key, name))
        return True

    def search(self, jql, max_results=25):
        self.jql = jql  # the query the poll actually sent
        return [make_issue()]
    def names(self): return [c[0] for c in self.calls]


class FakeGitHub:
    def __init__(self):
        self.opened: list[dict] = []

    def open_draft_pr(self, repo, branch, base, title, body, labels=None):
        self.opened.append({"repo": repo, "branch": branch, "base": base, "title": title, "body": body})
        return PullRequest(1, f"https://github.com/{repo}/pull/1")


class FakeEngine:
    """Answers the readiness prompt, and 'implements' by writing a file (or not)."""

    def __init__(
        self,
        ready=True,
        edits: dict[str, str] | None = None,
        fail=False,
        repair_edits: dict[str, str] | None = None,
        repair_fail=False,
        commits=False,
        marker: Path | None = None,
        description: str = "ok",  # ok | garbage | fail: how the PR-description call behaves
        route: str = TASK_ROUTE,  # raw reply to the routing prompt
        stages: dict[str, dict[str, str]] | None = None,  # prompt prefix -> files that stage writes
        stage_replies: dict[str, str] | None = None,  # prompt prefix -> reply text (default "done")
    ):
        self.route = route
        self.stages = stages or {}
        self.stage_replies = stage_replies or {}
        self.description = description
        self.ready = ready
        self.edits = {"src/app.txt": "hello electa\n"} if edits is None else edits
        self.fail = fail
        self.repair_edits = repair_edits or {}
        self.repair_fail = repair_fail
        self.commits = commits  # simulate an agent that commits despite being told not to
        self.marker = marker  # file a setup command creates; lets tests see setup ran first
        self.marker_seen: bool | None = None
        self.prompts: list[str] = []

    @property
    def repair_prompts(self) -> list[str]:
        return [p for p in self.prompts if p.startswith("REPAIR")]

    def run(self, workdir: Path, prompt: str, timeout_s: int) -> EngineResult:
        self.prompts.append(prompt)
        if self.fail:
            return EngineResult(ok=False, error="credits exhausted")
        if prompt.startswith("READINESS CHECK"):
            body = '{"ready": true, "questions": []}' if self.ready else '{"ready": false, "questions": ["Which page?"]}'
            return EngineResult(ok=True, response=body)
        if prompt.startswith("ROUTING"):
            return EngineResult(ok=True, response=self.route)
        for prefix, files in self.stages.items():  # bugfix and Spec Kit stages
            if prompt.startswith(prefix):
                for rel, content in files.items():
                    target = workdir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                return EngineResult(ok=True, response=self.stage_replies.get(prefix, "done"))
        if prompt.startswith("PR DESCRIPTION"):
            if self.description == "fail":
                return EngineResult(ok=False, error="description call crashed")
            if self.description == "garbage":
                return EngineResult(ok=True, response="Sure, here is a PR about greetings.")
            return EngineResult(ok=True, response=FILLED)
        if prompt.startswith("REPAIR"):
            if self.repair_fail:
                return EngineResult(ok=False, error="repair crashed")
            for rel, content in self.repair_edits.items():
                target = workdir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            return EngineResult(ok=True, response="Fixed it.")
        self.marker_seen = self.marker.exists() if self.marker else None
        for rel, content in self.edits.items():
            target = workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        if self.commits:
            git("add", "-A", cwd=workdir)
            git(*GIT_ID, "commit", "-m", "agent's own commit", cwd=workdir)
        return EngineResult(ok=True, response="Changed the greeting.")


def make_issue(key="ELE-1", summary="Fix greeting", project="ELE", description="Say hello electa", **fields) -> Issue:
    return Issue(key=key, summary=summary, description=description, project=project, **fields)


@pytest.fixture
def build(tmp_path: Path, origin: Path, cfg: Config):
    def _build(engine: FakeEngine, dry_run: bool = False):
        jira, gh = FakeJira(), FakeGitHub()
        repos = RepoManager(cfg.work_dir, remote_url=lambda name: str(origin))
        pipeline = Pipeline(cfg, jira, repos, gh, engine, State(cfg.work_dir / "s.sqlite"), Notifier(), dry_run)
        return pipeline, jira, gh, repos

    return _build
