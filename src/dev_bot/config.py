from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

ALL_WORKFLOWS = ("bugfix", "feature", "amend", "task")
_JIRA_PROJECT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PROJECTS_TOKEN = "{projects}"


class ConfigError(ValueError):
    """The config file is unusable; the message says which setting and why."""


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

    # -- what makes one repo different from another ---------------------------------------------
    # Which workflows this repo runs. A repo without Spec Kit keeps only ["bugfix", "task"].
    workflows: list[str] = field(default_factory=lambda: list(ALL_WORKFLOWS))
    # Extra environment for setup and check commands; "{work_dir}" expands to the bot's work_dir.
    env: dict[str, str] = field(default_factory=dict)
    # Repo skills offered to the engine (see skills.py): where to look, which to offer, how many.
    skills_dirs: list[str] = field(default_factory=lambda: [".agents/skills", ".claude/skills"])
    skills_include: list[str] = field(default_factory=list)  # fnmatch on skill name; empty = all
    skills_exclude: list[str] = field(default_factory=list)  # fnmatch on skill name
    skills_max: int = 30
    bugfix_skill: str = ""  # name of the repo's own bugfix skill, if it has one
    read_first: list[str] = field(default_factory=list)  # repo-relative files the engine reads before coding
    instructions: str = ""  # extra rules for the engine, specific to this repo
    transitions: dict[str, str] = field(default_factory=dict)  # per-repo override of [jira.transitions]

    @property
    def spec_aware(self) -> bool:
        """Does this repo keep Spec Kit specs the bot can amend (and a bugfix may need to update)?"""
        return "amend" in self.workflows or "feature" in self.workflows

    def allows(self, workflow: str) -> bool:
        return workflow in self.workflows

    def command_env(self, work_dir: Path) -> dict[str, str]:
        """`env` with "{work_dir}" expanded, for the setup and check commands."""
        return {k: v.replace("{work_dir}", work_dir.as_posix()) for k, v in self.env.items()}

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

    def transition(self, jira_project: str, which: str) -> str:
        """Jira transition name for `which`, honoring the project's repo override."""
        repo = self.repo_for(jira_project)
        if repo is not None and which in repo.transitions:
            return repo.transitions[which]
        return self.jira.transitions[which]

    def search_jql(self) -> str:
        """The poll query. A "{projects}" token expands to every configured repo's Jira projects."""
        projects = list(dict.fromkeys(p for repo in self.repos for p in repo.jira_projects))
        clause = "project in (" + ", ".join(f'"{p}"' for p in projects) + ")"
        return self.jira.jql.replace(PROJECTS_TOKEN, clause)


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


def _repo_from_toml(raw: dict) -> RepoConfig:
    known = {f.name for f in fields(RepoConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:  # a typo here would otherwise be silently ignored, or crash with a bare TypeError
        raise ConfigError(f"[[repos]] {raw.get('name', '?')}: unknown setting(s) {', '.join(unknown)}")
    if "name" not in raw:
        raise ConfigError("[[repos]] entry without a name")
    return RepoConfig(**raw)


def _validate(jira: JiraConfig, repos: list[RepoConfig]) -> None:
    if not repos:
        raise ConfigError("no [[repos]] configured")
    owner: dict[str, str] = {}
    for repo in repos:
        where = f"[[repos]] {repo.name}"
        if "/" not in repo.name:
            raise ConfigError(f'{where}: name must be "owner/repo"')
        if not repo.workflows:
            raise ConfigError(f"{where}: workflows must not be empty")
        bad = [w for w in repo.workflows if w not in ALL_WORKFLOWS]
        if bad:
            raise ConfigError(f"{where}: unknown workflow(s) {', '.join(bad)}; choose from {', '.join(ALL_WORKFLOWS)}")
        for project in repo.jira_projects:
            if not _JIRA_PROJECT.match(project):
                raise ConfigError(f"{where}: {project!r} is not a Jira project key")
            if project in owner:  # repo_for() would silently pick the first, so refuse the ambiguity
                raise ConfigError(f"Jira project {project} maps to both {owner[project]} and {repo.name}")
            owner[project] = repo.name
    if PROJECTS_TOKEN in jira.jql and not owner:
        raise ConfigError(f"jql uses {PROJECTS_TOKEN} but no repo lists jira_projects")


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

    repos = [_repo_from_toml(r) for r in raw.get("repos", [])]
    _validate(jira, repos)
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
