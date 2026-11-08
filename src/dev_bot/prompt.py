"""Renders the engine prompts. The wording lives in `prompts/*.md` (Jinja templates); this module only
supplies their variables, so a prompt can be read and edited as plain text."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import RepoConfig
from .jira import Issue

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "prompts"),
    undefined=StrictUndefined,  # a typo in a template must fail loudly, not send the engine a blank
    trim_blocks=True,  # lines holding only a {% tag %} leave no blank line behind
    lstrip_blocks=True,
    autoescape=False,  # prompts are plain text, not HTML
)


def render(name: str, /, **context: object) -> str:
    return _env.get_template(name).render(**context)


def build_task_prompt(issue: Issue, repo: RepoConfig, agents_md: str = "") -> str:
    return render("task.md", issue=issue, repo=repo, agents_md=agents_md)


def build_repair_prompt(issue: Issue, repo: RepoConfig, command: str, output: str) -> str:
    return render("repair.md", issue=issue, repo=repo, command=command, output=output)


def build_triage_prompt(issue: Issue) -> str:
    return render("triage.md", issue=issue)


def build_pr_description_prompt(issue: Issue, jira_base_url: str, template: str, diff: str) -> str:
    return render("pr_description.md", issue=issue, jira_base_url=jira_base_url, template=template, diff=diff)


ROUTING_CHOICES = ("bugfix", "amend", "feature", "task")


def build_routing_prompt(issue: Issue, specs: list, workflows: list[str] | tuple[str, ...] = ROUTING_CHOICES) -> str:
    """`workflows` is what the classifier may pick: the ones the ticket's repo allows."""
    return render("routing.md", issue=issue, specs=specs, workflows=list(workflows))


def build_bugfix_prompt(issue: Issue, repo: RepoConfig, agents_md: str = "", spec_hint: str = "") -> str:
    return render("bugfix.md", issue=issue, repo=repo, agents_md=agents_md, spec_hint=spec_hint)


def build_stage_prompt(
    stage: str, issue: Issue, repo: RepoConfig, spec_folder: str, jira_url: str, agents_md: str = ""
) -> str:
    """One stage of `feature` (new spec folder) or `amend` (existing spec folder), keyed like "feature.plan"."""
    return render(
        f"stages/{stage.replace('.', '_')}.md",
        issue=issue,
        repo=repo,
        agents_md=agents_md,
        spec_folder=spec_folder,
        spec_path=f"{repo.spec_dir.strip('/')}/{spec_folder}",
        jira_url=jira_url,
    )


def build_pr_body(issue_url: str, issue: Issue, results: list, repairs: int = 0) -> str:
    """Fallback description, used only when the repo has no PR template or filling it failed."""
    parts = [f"Jira: [{issue.key}]({issue_url}) - {issue.summary}"]
    passed = [f"- [x] `{r.command}`" for r in results if r.ok]
    if passed:
        note = f" after {repairs} automatic fix round(s)" if repairs else ""
        parts.append(f"## Checks run by the bot (all passed{note})\n" + "\n".join(passed))
    parts.append("_Opened as a draft by dev-bot. Please review before merging._")
    return "\n\n".join(parts)
