"""Two repos, two Jira projects, one pipeline: each ticket gets its own repo's checks, env, branch, PR and skills."""

from __future__ import annotations

from pathlib import Path

from conftest import (
    GIT_ID,
    FakeEngine,
    FakeGitHub,
    FakeJira,
    git,
    make_issue,
    write_skill,
)

from dev_bot.config import Config, JiraConfig, RepoConfig
from dev_bot.notify import Notifier
from dev_bot.pipeline import Pipeline
from dev_bot.repo import RepoManager
from dev_bot.state import State


def seed_origin(tmp_path: Path, name: str, with_skill: bool) -> Path:
    bare, seed = tmp_path / f"{name}.git", tmp_path / f"{name}-seed"
    git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("init", "-b", "main", str(seed), cwd=tmp_path)
    (seed / "src").mkdir()
    (seed / "src" / "app.txt").write_text("hello\n")
    if with_skill:
        write_skill(seed, ".agents/skills", "build-widget", "name: build-widget\ndescription: How this repo builds a widget.")
    git("add", "-A", cwd=seed)
    git(*GIT_ID, "commit", "-m", "init", cwd=seed)
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    return bare


def test_each_repo_runs_its_own_checks_env_transitions_branch_pr_and_skills(tmp_path):
    app_origin = seed_origin(tmp_path, "app", with_skill=False)
    design_origin = seed_origin(tmp_path, "design", with_skill=True)
    # The design repo's check passes only if its own `env` reached the command.
    design_check = "python -c \"import os, sys; sys.exit(0 if os.environ.get('DESIGN_ENV') == 'ok' else 1)\""
    cfg = Config(
        jira=JiraConfig(base_url="https://x.atlassian.net", jql="{projects} AND labels = bot"),
        repos=[
            RepoConfig(name="acme/app", jira_projects=["ELE"], check_commands=['python -c "print(1)"'], allowed_paths=["src/"]),
            RepoConfig(
                name="acme/design",
                jira_projects=["DS"],
                workflows=["bugfix", "task"],
                check_commands=[design_check],
                env={"DESIGN_ENV": "ok"},
                allowed_paths=["src/"],
                transitions={"in_review": "Peer Review"},
            ),
        ],
        work_dir=tmp_path / "work",
    )
    urls = {"acme/app": str(app_origin), "acme/design": str(design_origin)}
    jira, gh, engine = FakeJira(), FakeGitHub(), FakeEngine()
    pipeline = Pipeline(
        cfg, jira, RepoManager(cfg.work_dir, remote_url=lambda name: urls[name]), gh, engine,
        State(cfg.work_dir / "s.sqlite"), Notifier(),
    )

    app_outcome = pipeline.process(make_issue(key="ELE-1", summary="Fix greeting", project="ELE"))
    design_outcome = pipeline.process(make_issue(key="DS-5", summary="Add widget", project="DS"))

    assert (app_outcome.status, design_outcome.status) == ("pr_opened", "pr_opened"), design_outcome.detail
    assert [(pr["repo"], pr["branch"]) for pr in gh.opened] == [
        ("acme/app", "bot/ELE-1-fix-greeting"),
        ("acme/design", "bot/DS-5-add-widget"),
    ]
    assert git("show", "bot/DS-5-add-widget:src/app.txt", cwd=design_origin) == "hello electa\n"
    assert "bot/DS-5-add-widget" not in git("branch", cwd=app_origin)  # nothing leaked into the other repo
    assert ("transition", "DS-5", "Peer Review") in jira.calls and ("transition", "ELE-1", "In Review") in jira.calls

    implement = {p.split(" in this repository")[0]: p for p in engine.prompts if p.startswith("You are implementing")}
    assert "build-widget" in implement["You are implementing Jira ticket DS-5"]
    assert "Repository skills" not in implement["You are implementing Jira ticket ELE-1"]
    app_routing, design_routing = [p for p in engine.prompts if p.startswith("ROUTING")]
    assert '"workflow": "bugfix|amend|feature|task"' in app_routing
    assert '"workflow": "bugfix|task"' in design_routing and "- feature:" not in design_routing
