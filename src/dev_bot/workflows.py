"""What each workflow asks of the engine: an ordered list of stages, each with an independent check.

A stage is one engine call. The pipeline runs the guardrails after every stage and then the stage's own
`verify`, so a Spec Kit run cannot reach the next stage (or a PR) with an artifact missing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, RepoConfig
from .jira import Issue
from .orchestrator import AMEND, BUGFIX, TASK, Route
from .prompt import build_bugfix_prompt, build_stage_prompt, build_task_prompt
from .repo import slugify
from .specs import is_check_task, next_folder, unfinished_tasks

Verify = Callable[[Path, list[str], str], "str | None"]  # (worktree, changed files, engine reply) -> problem
Halts = Callable[[str], "str | None"]  # engine reply -> reason to hand the ticket back to a human

_CLASS_D = re.compile(r"^\s*CLASS:\s*D\b", re.IGNORECASE | re.MULTILINE)
_ESCALATE = re.compile(r"^\s*ESCALATE:\s*amend\b", re.IGNORECASE | re.MULTILINE)


@dataclass
class Stage:
    name: str
    prompt: str
    verify: Verify = lambda worktree, files, reply: None
    halts: Halts = lambda reply: None


@dataclass
class Plan:
    workflow: str
    repo: RepoConfig  # the repo's rules as they apply to this workflow
    stages: list[Stage] = field(default_factory=list)


def build_plan(
    route: Route, issue: Issue, repo: RepoConfig, worktree: Path, cfg: Config, agents_md: str
) -> Plan:
    rules = repo.for_workflow(route.workflow)
    if route.workflow == TASK:
        return Plan(TASK, rules, [Stage("implement", build_task_prompt(issue, rules, agents_md))])
    if route.workflow == BUGFIX:
        hint = f"{repo.spec_dir.strip('/')}/{route.spec.folder}" if route.spec else ""
        return Plan(BUGFIX, rules, [_bugfix_stage(issue, rules, agents_md, hint, cfg.jira.workflow_labels[AMEND])])

    spec_root = repo.spec_dir.strip("/")
    if route.workflow == AMEND:
        assert route.spec is not None  # route_ticket guarantees it
        folder, kind = route.spec.folder, "amend"
    else:
        folder, kind = next_folder(worktree, repo.spec_dir, slugify(issue.summary)), "feature"
    d = f"{spec_root}/{folder}"
    tag = f"[{issue.key}]" if kind == "amend" else ""
    jira_url = f"{cfg.jira.base_url}/browse/{issue.key}"

    def stage(name: str, verify: Verify) -> Stage:
        prompt = build_stage_prompt(f"{kind}.{name}", issue, rules, folder, jira_url, agents_md)
        return Stage(name, prompt, lambda w, files, reply: _own_folder_only(files, spec_root, d) or verify(w, files, reply))

    def touched(*names: str) -> Verify:
        return lambda w, files, reply: next((f"{n} was not written" for n in names if f"{d}/{n}" not in files), None)

    def tasks_written(w: Path, files: list[str], reply: str) -> str | None:
        problem = touched("tasks.md")(w, files, reply)
        if problem:
            return problem
        text = _read(w / d / "tasks.md")
        if tag:  # amend: the tasks for this ticket must be identifiable, or implement cannot be scoped
            return None if tag in text else f"tasks.md has no task tagged {tag}"
        return None if unfinished_tasks(text) else "tasks.md has no task lines"

    def implemented(w: Path, files: list[str], reply: str) -> str | None:
        left = [t for t in unfinished_tasks(_read(w / d / "tasks.md"), tag) if not is_check_task(t)]
        if left:
            return f"{len(left)} task(s) left unfinished, first: {left[0]}"
        if all(f.startswith(f"{spec_root}/") for f in files):
            return "only spec files changed; no code was written"
        return None

    plan_verify = touched("plan.md") if kind == "feature" else (lambda w, files, reply: None)
    spec_verify = touched("spec.md", "research.md") if kind == "feature" else touched("spec.md")
    stages = [
        stage("specify" if kind == "feature" else "spec", spec_verify),
        stage("plan", plan_verify),
        stage("tasks", tasks_written),
        stage("implement", implemented),
    ]
    return Plan(route.workflow, rules, stages)


def _bugfix_stage(issue: Issue, repo: RepoConfig, agents_md: str, spec_hint: str, amend_label: str) -> Stage:
    def halts(reply: str) -> str | None:
        if _ESCALATE.search(reply):  # a wide spec change is a spec amendment, not a bugfix a human has not reviewed
            return f"{reply.strip()}\nTo continue, add the `{amend_label}` label and quote the spec folder in the ticket."
        return reply.strip() if _CLASS_D.search(reply) else None

    def verify(worktree: Path, files: list[str], reply: str) -> str | None:
        if not any("test" in f.lower() for f in files):
            return "No regression test was added or changed."
        return None

    return Stage("bugfix", build_bugfix_prompt(issue, repo, agents_md, spec_hint), verify, halts)


def _own_folder_only(files: list[str], spec_root: str, own: str) -> str | None:
    stray = [f for f in files if f.startswith(f"{spec_root}/") and not f.startswith(f"{own}/")]
    return f"Touches another spec folder: {stray[0]}" if stray else None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
