"""Per-repo settings: the config schema and its validation, the {projects} query, per-repo env and transitions."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeEngine

from dev_bot.config import Config, ConfigError, JiraConfig, RepoConfig, load_config


def test_new_repo_settings_default_to_the_previous_behaviour():
    repo = RepoConfig(name="a/b")
    assert repo.workflows == ["bugfix", "feature", "amend", "task"] and repo.spec_aware
    assert (repo.env, repo.skills_include, repo.skills_exclude, repo.read_first, repo.transitions) == ({}, [], [], [], {})
    assert repo.skills_dirs == [".agents/skills", ".claude/skills"] and repo.instructions == ""
    assert not RepoConfig(name="a/b", workflows=["bugfix", "task"]).spec_aware
    assert RepoConfig(name="a/b", workflows=["task", "feature"]).spec_aware


def two_repo_cfg(tmp_path: Path, jql: str = "{projects} AND labels = bot") -> Config:
    return Config(
        jira=JiraConfig(base_url="https://x.atlassian.net", jql=jql),
        repos=[
            RepoConfig(name="acme/app", jira_projects=["VS", "VX"]),
            RepoConfig(name="acme/design", jira_projects=["DS"], transitions={"in_review": "Peer Review"}),
        ],
        work_dir=tmp_path / "work",
    )


def test_projects_token_expands_to_every_repos_projects(tmp_path):
    assert two_repo_cfg(tmp_path).search_jql() == 'project in ("VS", "VX", "DS") AND labels = bot'
    assert two_repo_cfg(tmp_path, "project = VS ORDER BY created").search_jql() == "project = VS ORDER BY created"


def test_a_repo_can_override_jira_transitions_for_its_projects(tmp_path):
    cfg = two_repo_cfg(tmp_path)
    assert cfg.transition("DS", "in_review") == "Peer Review"
    assert cfg.transition("DS", "in_progress") == "In Progress"  # not overridden: the global name
    assert cfg.transition("VS", "in_review") == "In Review"
    assert cfg.transition("UNKNOWN", "in_review") == "In Review"


def test_command_env_expands_the_work_dir(tmp_path):
    repo = RepoConfig(name="a/b", env={"npm_config_cache": "{work_dir}/npm-cache", "CI_FLAG": "1"})
    env = repo.command_env(tmp_path / "work")
    assert env == {"npm_config_cache": f"{(tmp_path / 'work').as_posix()}/npm-cache", "CI_FLAG": "1"}


JIRA = '[jira]\nbase_url = "https://x.atlassian.net"\njql = "{jql}"\n'


def load(tmp_path: Path, repos: str, jql: str = "project = A") -> Config:
    path = tmp_path / "config.toml"
    path.write_text(JIRA.format(jql=jql) + repos, encoding="utf-8")
    return load_config(path, env_file=tmp_path / "none.env")


@pytest.mark.parametrize(
    "repos,message",
    [
        ('[[repos]]\nname = "a/b"\nbogus = 1\n', "unknown setting(s) bogus"),
        ('[[repos]]\nname = "a/b"\nworkflows = ["bugfix", "nope"]\n', "unknown workflow(s) nope"),
        ('[[repos]]\nname = "a/b"\nworkflows = []\n', "workflows must not be empty"),
        ('[[repos]]\nname = "ab"\n', 'name must be "owner/repo"'),
        ('[[repos]]\nname = "a/b"\njira_projects = ["V-S"]\n', "not a Jira project key"),
        ('[[repos]]\nname = "a/b"\njira_projects = ["VS"]\n[[repos]]\nname = "c/d"\njira_projects = ["VS"]\n', "VS maps to both a/b and c/d"),
        ("", "no [[repos]] configured"),
    ],
)
def test_load_config_rejects_what_would_misbehave_later(tmp_path, repos, message):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path, repos)
    assert message in str(exc.value)


def test_projects_token_needs_a_repo_with_projects(tmp_path):
    with pytest.raises(ConfigError, match="no repo lists jira_projects"):
        load(tmp_path, '[[repos]]\nname = "a/b"\n', jql="{projects} AND labels = bot")


def test_run_once_polls_with_every_repos_projects(tmp_path, build, cfg):
    cfg.jira.jql = "{projects} AND labels = bot"
    pipeline, jira, _, _ = build(FakeEngine())
    pipeline.run_once()
    assert jira.jql == 'project in ("ELE") AND labels = bot'
