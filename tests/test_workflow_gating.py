"""A repo chooses which workflows it runs, so a repo without Spec Kit is never sent down a spec path."""

from __future__ import annotations

import pytest
from conftest import make_issue

from dev_bot import orchestrator
from dev_bot.config import RepoConfig
from dev_bot.engine.base import EngineResult
from dev_bot.jira import Issue
from dev_bot.prompt import build_bugfix_prompt, build_routing_prompt
from dev_bot.workflows import build_plan


@pytest.fixture
def issue() -> Issue:
    return make_issue(key="DS-1", summary="Add badge", project="DS", description="Make a badge")


class Classifier:
    def __init__(self, reply='{"workflow": "task", "reason": "small"}', ok=True):
        self.reply, self.ok, self.prompts = reply, ok, []

    def run(self, workdir, prompt, timeout_s):
        self.prompts.append(prompt)
        return EngineResult(ok=self.ok, response=self.reply, error=None if self.ok else "boom")


@pytest.fixture
def spec_free(tmp_path) -> RepoConfig:
    """A repo with no Spec Kit; its tree even has a folder that looks like a spec."""
    spec = tmp_path / "specs" / "001-greeting"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("# Feature Specification: Greeting\n\n**Jira Key**: [DS-1](https://x/DS-1)\n")
    return RepoConfig(name="acme/design", jira_projects=["DS"], workflows=["bugfix", "task"])


def route_in(repo, root, cfg, issue, engine=None):
    engine = engine or Classifier()
    return orchestrator.route_ticket(issue, root, repo, cfg, engine), engine


def test_a_label_for_a_workflow_the_repo_does_not_run_goes_back_to_a_human(tmp_path, cfg, spec_free):
    with pytest.raises(orchestrator.RoutingError) as exc:
        route_in(spec_free, tmp_path, cfg, make_issue(labels=["bot-feature"]))
    assert "does not run" in exc.value.questions[0] and "bugfix, task" in exc.value.questions[0]


def test_a_bug_goes_to_task_when_the_repo_has_no_bugfix_workflow(tmp_path, cfg):
    repo = RepoConfig(name="acme/x", workflows=["task"])
    route, engine = route_in(repo, tmp_path, cfg, make_issue(issue_type="Bug"))
    assert route.workflow == "task" and not engine.prompts


def test_a_single_allowed_workflow_needs_no_classifier_call(tmp_path, cfg):
    route, engine = route_in(RepoConfig(name="acme/x", workflows=["task"]), tmp_path, cfg, make_issue())
    assert route.workflow == "task" and not engine.prompts


def test_a_spec_free_repo_never_amends_even_if_the_ticket_quotes_a_spec(tmp_path, cfg, spec_free):
    issue = make_issue(key="DS-1", description="Change the greeting in specs/001-greeting")
    route, engine = route_in(spec_free, tmp_path, cfg, issue)
    assert route.workflow == "task" and route.spec is None
    assert "Spec Kit specs" not in engine.prompts[0]  # it was not even shown the folder
    assert '"workflow": "bugfix|task"' in engine.prompts[0]


@pytest.mark.parametrize("reply", ['{"workflow": "feature"}', '{"workflow": "amend", "spec": "001-greeting"}'])
def test_a_classifier_answer_outside_the_allowed_set_falls_back_to_task(tmp_path, cfg, spec_free, reply):
    route, _ = route_in(spec_free, tmp_path, cfg, make_issue(), Classifier(reply))
    assert route.workflow == "task"


def test_routing_prompt_offers_only_the_workflows_the_repo_runs(issue):
    prompt = build_routing_prompt(issue, [], ["bugfix", "task"])
    assert "- bugfix:" in prompt and "- task:" in prompt
    assert "- feature:" not in prompt and "- amend:" not in prompt and "Spec Kit specs" not in prompt
    assert '"workflow": "bugfix|task"' in prompt and '"spec"' not in prompt
    full = build_routing_prompt(issue, [])
    assert all(f"- {wf}:" in full for wf in ("bugfix", "amend", "feature", "task")) and "bugfix|amend|feature|task" in full


def test_bugfix_prompt_for_a_repo_without_specs_has_no_spec_classes(issue):
    prompt = build_bugfix_prompt(issue, RepoConfig(name="a/b", workflows=["bugfix", "task"]), "", "")
    assert "B = stale spec" not in prompt and "ESCALATE" not in prompt and "constitution" not in prompt
    assert "`CLASS: A` or `CLASS: D`" in prompt
    spec_prompt = build_bugfix_prompt(issue, RepoConfig(name="a/b"), "", "specs/001-x")
    assert "B = stale spec" in spec_prompt and "ESCALATE: amend" in spec_prompt and "specs/001-x" in spec_prompt


def test_escalate_only_hands_back_when_the_repo_can_amend(tmp_path, cfg, issue):
    reply = "Wide spec change.\nESCALATE: amend\nCLASS: B"
    route = orchestrator.Route("bugfix", "test")
    for repo, handed_back in (
        (RepoConfig(name="a/b", workflows=["bugfix", "task"]), False),
        (RepoConfig(name="a/b"), True),
    ):
        halts = build_plan(route, issue, repo, tmp_path, cfg, "").stages[0].halts
        assert bool(halts(reply)) is handed_back
