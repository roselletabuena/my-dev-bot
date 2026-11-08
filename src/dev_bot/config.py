from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import tomllib


@dataclass
class RepoConfig:
    name: str  # "owner/repo"
    default_branch: str = "main"
    jira_projects: list[str] = field(default_factory=list)
    setup_commands: list[str] = field(default_factory=list)
    check_commands: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)  # empty = anywhere not denied
    denied_paths: list[str] = field(default_factory=list)
    max_diff_lines: int = 300
    spec_dir: str = "specs"  # where Spec Kit keeps one NNN-slug folder per feature
    spec_max_diff_lines: int = 1500  # spec workflows write docs plus code, so they get a larger cap

    def for_workflow(self, workflow: str) -> RepoConfig:
        """This repo's rules as they apply to `workflow`: spec workflows may also write the spec folder."""
        if workflow not in ("feature", "amend"):
            return self
        spec_root = self.spec_dir.strip("/") + "/"
        allowed = self.allowed_paths + [spec_root] if self.allowed_paths else []
        return replace(self, allowed_paths=allowed, max_diff_lines=self.spec_max_diff_lines)


@dataclass
class JiraConfig:
    base_url: str
    jql: str
    claimed_label: str = "bot-claimed"
    # A ticket carrying one of these labels skips classification and runs that workflow.
    workflow_labels: dict[str, str] = field(
        default_factory=lambda: {"bugfix": "bot-bugfix", "feature": "bot-feature", "amend": "bot-amend"}
    )
    transitions: dict[str, str] = field(
        default_factory=lambda: {
            "in_progress": "In Progress",
            "in_review": "In Review",
            "needs_info": "Needs Info",
        }
    )


@dataclass
class Secrets:
    jira_email: str = ""
    jira_api_token: str = ""
    github_token: str = ""  # empty -> github.resolve_token() falls back to `gh auth token`
    slack_webhook: str = ""


@dataclass
class Config:
    jira: JiraConfig
    repos: list[RepoConfig]
    secrets: Secrets = field(default_factory=Secrets)
    engine: str = "antigravity"
    engine_timeout_s: int = 900
    max_tickets_per_run: int = 3
    daily_cap: int = 10
    repair_rounds: int = 2  # how many times a failed check is fed back to the engine; 0 disables
    work_dir: Path = field(default_factory=lambda: Path.home() / ".dev-bot")
    git_user_name: str = "dev-bot"
    git_user_email: str = "dev-bot@users.noreply.github.com"

    @property
    def paused_file(self) -> Path:
        return self.work_dir / ".bot-paused"

    def repo_for(self, jira_project: str) -> RepoConfig | None:
        for repo in self.repos:
            if jira_project in repo.jira_projects:
                return repo
        return None


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: Path, env_file: Path | None = None) -> Config:
    load_dotenv(env_file or path.parent / ".env")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    jira_raw = raw["jira"]
    jira = JiraConfig(
        base_url=jira_raw["base_url"].rstrip("/"),
        jql=jira_raw["jql"],
        claimed_label=jira_raw.get("claimed_label", "bot-claimed"),
    )
    jira.transitions.update(jira_raw.get("transitions", {}))
    jira.workflow_labels.update(jira_raw.get("workflow_labels", {}))

    repos = [RepoConfig(**r) for r in raw.get("repos", [])]
    secrets = Secrets(
        jira_email=os.environ.get("JIRA_EMAIL", ""),
        jira_api_token=os.environ.get("JIRA_API_TOKEN", ""),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        slack_webhook=os.environ.get("SLACK_WEBHOOK", ""),
    )

    scalar_keys = (
        "engine",
        "engine_timeout_s",
        "max_tickets_per_run",
        "daily_cap",
        "repair_rounds",
        "git_user_name",
        "git_user_email",
    )
    extra = {k: raw[k] for k in scalar_keys if k in raw}
    if "work_dir" in raw:
        extra["work_dir"] = Path(raw["work_dir"]).expanduser()
    return Config(jira=jira, repos=repos, secrets=secrets, **extra)
