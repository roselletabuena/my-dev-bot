from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import SEED_SPEC, FakeEngine, git, make_issue

from dev_bot import orchestrator
from dev_bot.config import RepoConfig
from dev_bot.engine.base import EngineResult
from dev_bot.specs import is_check_task, list_specs, next_folder, unfinished_tasks

BRANCH = "bot/ELE-1-fix-greeting"


# -- specs -------------------------------------------------------------------------------------
@pytest.fixture
def specs_root(tmp_path: Path) -> Path:
    for folder, key in (("001-greeting", "ELE-9"), ("002-farewell", "ELE-10")):
        d = tmp_path / "specs" / folder
        d.mkdir(parents=True)
        (d / "spec.md").write_text(SEED_SPEC.replace("ELE-9", key))
    (tmp_path / "specs" / "notes").mkdir()  # not a numbered spec folder
    return tmp_path


def test_list_specs_reads_title_and_first_jira_key(specs_root):
    (specs_root / "specs" / "002-farewell" / "spec.md").write_text(
        "# Feature Specification: Farewell\n\n**Jira Key**: `ELE-10` (Parent Epic: `ELE-2`)\n"
    )
    specs = list_specs(specs_root, "specs")
    assert [(s.folder, s.title, s.jira_key) for s in specs] == [
        ("001-greeting", "Greeting", "ELE-9"),
        ("002-farewell", "Farewell", "ELE-10"),
    ]


def test_next_folder_is_one_past_the_highest_number(specs_root):
    assert next_folder(specs_root, "specs", "voting") == "003-voting"
    assert next_folder(specs_root / "missing", "specs", "voting") == "001-voting"


def test_unfinished_tasks_can_be_scoped_to_a_ticket():
    text = "- [x] T001 done\n- [ ] T002 old\n- [ ] T003 [ELE-1] new\n- [x] T004 [ELE-1] done\n"
    assert unfinished_tasks(text) == ["- [ ] T002 old", "- [ ] T003 [ELE-1] new"]
    assert unfinished_tasks(text, "[ELE-1]") == ["- [ ] T003 [ELE-1] new"]


def test_check_tasks_are_recognised_narrowly():
    gates = "- [ ] T032 [ELE-1] Run quality gates (`npm run test:unit`, `npm run lint`) in src/a.ts"
    assert is_check_task(gates)
    assert is_check_task("- [ ] T034 [ELE-1] Run Vitest (`npm run test:unit`) and `npx eslint`")
    assert not is_check_task("- [ ] T003 [ELE-1] Add an `npm run seed` script to package.json")
    assert not is_check_task("- [ ] T004 [ELE-1] Run the aggregation in src/totals.ts")


def test_spec_workflows_may_write_the_spec_folder_with_a_larger_cap():
    repo = RepoConfig(name="a/b", allowed_paths=["src/"], max_diff_lines=300)
    assert repo.for_workflow("bugfix") is repo
    spec_rules = repo.for_workflow("feature")
    assert spec_rules.allowed_paths == ["src/", "specs/"]
    assert spec_rules.max_diff_lines == 1500
    assert repo.allowed_paths == ["src/"]  # the original is untouched
    assert RepoConfig(name="a/b").for_workflow("amend").allowed_paths == []  # "anywhere" stays "anywhere"


# -- routing -----------------------------------------------------------------------------------
class Classifier:
    def __init__(self, reply: str, ok: bool = True):
        self.reply, self.ok, self.prompts = reply, ok, []

    def run(self, workdir, prompt, timeout_s):
        self.prompts.append(prompt)
        return EngineResult(ok=self.ok, response=self.reply, error=None if self.ok else "boom")


def route(issue, root, cfg, repo_cfg, reply='{"workflow": "feature", "reason": "new"}', ok=True):
    engine = Classifier(reply, ok)
    return orchestrator.route_ticket(issue, root, repo_cfg, cfg, engine), engine


def test_bug_issue_type_routes_to_bugfix_without_asking_the_engine(specs_root, cfg, repo_cfg):
    r, engine = route(make_issue(issue_type="Bug"), specs_root, cfg, repo_cfg)
    assert r.workflow == "bugfix" and not engine.prompts


def test_workflow_label_overrides_issue_type(specs_root, cfg, repo_cfg):
    r, engine = route(make_issue(issue_type="Bug", labels=["bot-feature"]), specs_root, cfg, repo_cfg)
    assert r.workflow == "feature" and not engine.prompts


def test_quoting_a_spec_folder_routes_to_amend(specs_root, cfg, repo_cfg):
    issue = make_issue(description="Change the greeting described in specs/001-greeting to be shorter")
    r, engine = route(issue, specs_root, cfg, repo_cfg)
    assert (r.workflow, r.spec.folder) == ("amend", "001-greeting") and not engine.prompts


def test_ticket_that_already_has_a_spec_routes_to_amend(specs_root, cfg, repo_cfg):
    r, _ = route(make_issue(key="ELE-10"), specs_root, cfg, repo_cfg)
    assert (r.workflow, r.spec.folder) == ("amend", "002-farewell")


def test_bugfix_is_pointed_at_the_spec_it_names(specs_root, cfg, repo_cfg):
    r, _ = route(make_issue(issue_type="Bug", description="see specs/002-farewell"), specs_root, cfg, repo_cfg)
    assert (r.workflow, r.spec.folder) == ("bugfix", "002-farewell")


def test_engine_classifies_what_the_signals_leave_open(specs_root, cfg, repo_cfg):
    r, engine = route(make_issue(), specs_root, cfg, repo_cfg)
    assert r.workflow == "feature"
    assert "001-greeting: Greeting (ticket ELE-9)" in engine.prompts[0]


def test_engine_amend_must_name_a_real_spec(specs_root, cfg, repo_cfg):
    good = '{"workflow": "amend", "spec": "002-farewell", "reason": "changes farewell"}'
    assert route(make_issue(), specs_root, cfg, repo_cfg, good)[0].spec.folder == "002-farewell"
    with pytest.raises(orchestrator.RoutingError):
        route(make_issue(), specs_root, cfg, repo_cfg, '{"workflow": "amend", "spec": "099-nope"}')


def test_amend_label_without_a_spec_asks_the_human(specs_root, cfg, repo_cfg):
    with pytest.raises(orchestrator.RoutingError) as exc:
        route(make_issue(labels=["bot-amend"]), specs_root, cfg, repo_cfg)
    assert "specs/015-core-voting-engine" in exc.value.questions[0]


@pytest.mark.parametrize("reply,ok", [("no idea", True), ('{"workflow": "rewrite"}', True), ("", False)])
def test_unusable_classification_falls_back_to_the_small_task_workflow(specs_root, cfg, repo_cfg, reply, ok):
    assert route(make_issue(), specs_root, cfg, repo_cfg, reply, ok)[0].workflow == "task"


# -- bugfix workflow ---------------------------------------------------------------------------
BUG_FIX = {"src/app.txt": "hello electa\n", "tests/app.test.txt": "regression\n"}


def bug(**kw):
    return make_issue(issue_type="Bug", **kw)


def test_bugfix_with_a_regression_test_opens_a_pr(build, repo_cfg):
    repo_cfg.allowed_paths.append("tests/")
    engine = FakeEngine(stages={"BUGFIX": BUG_FIX}, stage_replies={"BUGFIX": "Root cause: x\nCLASS: A"})
    pipeline, jira, gh, _ = build(engine)
    outcome = pipeline.process(bug())

    assert (outcome.status, outcome.workflow) == ("pr_opened", "bugfix")
    assert any(c[0] == "comment" and "Workflow: bugfix" in c[2] for c in jira.calls)
    assert not any(p.startswith("ROUTING") for p in engine.prompts)  # Bug type needs no classification


def test_bugfix_without_a_regression_test_fails(build):
    engine = FakeEngine(stages={"BUGFIX": {"src/app.txt": "hello electa\n"}})
    outcome = build(engine)[0].process(bug())
    assert outcome.status == "failed" and "regression test" in outcome.detail


def test_bugfix_that_needs_a_decision_goes_back_to_the_human_without_code(build):
    reply = "Fixing this would change the vote-count contract.\nCLASS: D"
    pipeline, jira, gh, _ = build(FakeEngine(stages={"BUGFIX": {}}, stage_replies={"BUGFIX": reply}))
    outcome = pipeline.process(bug())

    assert (outcome.status, outcome.workflow) == ("needs_info", "bugfix")
    assert "vote-count contract" in outcome.detail
    assert ("remove_label", "ELE-1", "bot-claimed") in jira.calls
    assert not gh.opened


def test_bugfix_with_a_wide_spec_change_is_escalated_to_a_human_without_code(build):
    reply = "Class B: the spec's whole tally section is stale.\nESCALATE: amend\nCLASS: B"
    pipeline, jira, gh, _ = build(FakeEngine(stages={"BUGFIX": {}}, stage_replies={"BUGFIX": reply}))
    outcome = pipeline.process(bug())

    assert (outcome.status, outcome.workflow) == ("needs_info", "bugfix")
    assert "tally section is stale" in outcome.detail and "`bot-amend` label" in outcome.detail
    assert ("remove_label", "ELE-1", "bot-claimed") in jira.calls
    assert not gh.opened


# -- feature workflow (new spec) ---------------------------------------------------------------
SPEC = "specs/002-fix-greeting"
FEATURE_ROUTE = '{"workflow": "feature", "spec": null, "reason": "new capability"}'


def feature_stages(**overrides):
    stages = {
        "SPECKIT SPECIFY": {f"{SPEC}/spec.md": "# spec\n", f"{SPEC}/research.md": "# research\n"},
        "SPECKIT PLAN": {f"{SPEC}/plan.md": "# plan\n"},
        "SPECKIT TASKS": {f"{SPEC}/tasks.md": "- [ ] T001 greet in src/app.txt\n"},
        "SPECKIT IMPLEMENT": {f"{SPEC}/tasks.md": "- [x] T001 greet in src/app.txt\n", "src/app.txt": "hello electa\n"},
    }
    stages.update(overrides)
    return stages


def test_feature_runs_every_stage_into_the_next_numbered_spec_folder(build, origin):
    engine = FakeEngine(route=FEATURE_ROUTE, stages=feature_stages())
    pipeline, jira, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert (outcome.status, outcome.workflow) == ("pr_opened", "feature")
    stage_prompts = [p.split(".")[0] for p in engine.prompts if p.startswith("SPECKIT")]
    assert stage_prompts == ["SPECKIT SPECIFY", "SPECKIT PLAN", "SPECKIT TASKS", "SPECKIT IMPLEMENT"]
    assert f"`{SPEC}/spec.md`" in engine.prompts[2]  # 001 exists, so the new spec is 002
    assert git("show", f"{BRANCH}:{SPEC}/plan.md", cwd=origin) == "# plan\n"
    assert git("show", f"{BRANCH}:src/app.txt", cwd=origin) == "hello electa\n"


def test_feature_stops_when_a_stage_skips_its_artifact(build):
    stages = feature_stages(**{"SPECKIT SPECIFY": {f"{SPEC}/spec.md": "# spec\n"}})  # no research.md
    engine = FakeEngine(route=FEATURE_ROUTE, stages=stages)
    outcome = build(engine)[0].process(make_issue())

    assert outcome.status == "failed" and "specify stage is incomplete" in outcome.detail
    assert not any(p.startswith("SPECKIT PLAN") for p in engine.prompts)  # never reached the next stage


def test_feature_with_unfinished_tasks_does_not_open_a_pr(build):
    stages = feature_stages(**{"SPECKIT IMPLEMENT": {"src/app.txt": "hello electa\n"}})  # tasks stay unticked
    pipeline, _, gh, _ = build(FakeEngine(route=FEATURE_ROUTE, stages=stages))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed" and "1 task(s) left unfinished" in outcome.detail
    assert not gh.opened


def test_feature_that_writes_only_docs_does_not_open_a_pr(build):
    stages = feature_stages(**{"SPECKIT IMPLEMENT": {f"{SPEC}/tasks.md": "- [x] T001 greet\n"}})
    outcome = build(FakeEngine(route=FEATURE_ROUTE, stages=stages))[0].process(make_issue())
    assert outcome.status == "failed" and "no code was written" in outcome.detail


def test_feature_may_not_edit_another_specs_folder(build):
    stages = feature_stages(**{"SPECKIT PLAN": {f"{SPEC}/plan.md": "# plan\n", "specs/001-greeting/spec.md": "oops\n"}})
    outcome = build(FakeEngine(route=FEATURE_ROUTE, stages=stages))[0].process(make_issue())
    assert outcome.status == "failed" and "another spec folder" in outcome.detail


def test_feature_spec_files_are_outside_the_task_workflow_allowlist(build):
    """The small-task workflow keeps its tight rules; only spec workflows may write specs/."""
    engine = FakeEngine(edits={"specs/002-x/spec.md": "x\n"})
    outcome = build(engine)[0].process(make_issue())
    assert outcome.status == "failed" and "Outside allowed paths" in outcome.detail


# -- amend workflow (existing spec) ------------------------------------------------------------
AMEND_SPEC = "specs/001-greeting"
AMEND_TEXT = "Shorten the greeting described in specs/001-greeting"


def amend_stages(**overrides):
    stages = {
        "SPECKIT AMEND SPEC": {f"{AMEND_SPEC}/spec.md": SEED_SPEC + "\n## Revisions\n- ELE-1 shortened\n"},
        "SPECKIT AMEND PLAN": {},
        "SPECKIT AMEND TASKS": {
            f"{AMEND_SPEC}/tasks.md": "- [x] T001 Say hello in src/app.txt\n- [ ] T002 [ELE-1] shorten in src/app.txt\n"
        },
        "SPECKIT AMEND IMPLEMENT": {
            f"{AMEND_SPEC}/tasks.md": "- [x] T001 Say hello in src/app.txt\n- [x] T002 [ELE-1] shorten in src/app.txt\n",
            "src/app.txt": "hi\n",
        },
    }
    stages.update(overrides)
    return stages


def test_amend_updates_the_existing_spec_instead_of_creating_one(build, origin):
    engine = FakeEngine(stages=amend_stages())
    pipeline, _, _, _ = build(engine)
    outcome = pipeline.process(make_issue(description=AMEND_TEXT))

    assert (outcome.status, outcome.workflow) == ("pr_opened", "amend")
    assert not any(p.startswith("ROUTING") for p in engine.prompts)  # the spec folder was quoted in the ticket
    assert "ELE-1 shortened" in git("show", f"{BRANCH}:{AMEND_SPEC}/spec.md", cwd=origin)
    assert git("ls-tree", "--name-only", f"{BRANCH}:specs", cwd=origin).split() == ["001-greeting"]


def test_amend_requires_tasks_tagged_with_the_ticket(build):
    untagged = {f"{AMEND_SPEC}/tasks.md": "- [x] T001 Say hello in src/app.txt\n- [ ] T002 shorten\n"}
    engine = FakeEngine(stages=amend_stages(**{"SPECKIT AMEND TASKS": untagged}))
    outcome = build(engine)[0].process(make_issue(description=AMEND_TEXT))
    assert outcome.status == "failed" and "no task tagged [ELE-1]" in outcome.detail


def test_amend_ignores_old_unfinished_tasks_but_not_its_own(build):
    old_open = "- [ ] T000 old leftover\n- [x] T002 [ELE-1] shorten in src/app.txt\n"
    done = amend_stages(**{"SPECKIT AMEND IMPLEMENT": {f"{AMEND_SPEC}/tasks.md": old_open, "src/app.txt": "hi\n"}})
    assert build(FakeEngine(stages=done))[0].process(make_issue(description=AMEND_TEXT)).status == "pr_opened"

    own_open = "- [x] T000 old\n- [ ] T002 [ELE-1] shorten in src/app.txt\n"
    left = amend_stages(**{"SPECKIT AMEND IMPLEMENT": {f"{AMEND_SPEC}/tasks.md": own_open, "src/app.txt": "hi\n"}})
    outcome = build(FakeEngine(stages=left))[0].process(make_issue(description=AMEND_TEXT))
    assert outcome.status == "failed" and "left unfinished" in outcome.detail


def test_amend_does_not_block_on_a_quality_gate_task(build):
    tasks = "- [x] T002 [ELE-1] shorten in src/app.txt\n- [ ] T003 [ELE-1] Run quality gates (`npm run lint`)\n"
    done = amend_stages(**{"SPECKIT AMEND IMPLEMENT": {f"{AMEND_SPEC}/tasks.md": tasks, "src/app.txt": "hi\n"}})
    assert build(FakeEngine(stages=done))[0].process(make_issue(description=AMEND_TEXT)).status == "pr_opened"


def test_amend_without_a_resolvable_spec_is_handed_back_with_a_question(build):
    pipeline, jira, gh, _ = build(FakeEngine())
    outcome = pipeline.process(make_issue(labels=["bot-amend"]))

    assert (outcome.status, outcome.workflow) == ("needs_info", "")
    assert ("remove_label", "ELE-1", "bot-claimed") in jira.calls
    assert not gh.opened


def test_router_failure_is_not_a_hard_stop(build):
    """A routing reply the bot cannot parse degrades to the small-task workflow, not a failed ticket."""
    outcome = build(FakeEngine(route=json.dumps("garbage")))[0].process(make_issue())
    assert (outcome.status, outcome.workflow) == ("pr_opened", "task")
