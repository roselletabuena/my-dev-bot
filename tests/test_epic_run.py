"""Running an Epic: its stories become a stack of draft PRs, and one story going wrong does not stop the rest."""

from __future__ import annotations

import json
import re

import pytest
from conftest import FakeEngine, FakeGitHub, FakeJira, git, make_issue
from test_epic import blocked_by, story

from dev_bot import main as cli
from dev_bot.engine.base import EngineResult
from dev_bot.epic import EpicVerdict
from dev_bot.epic_run import format_run, run_epic
from dev_bot.notify import Notifier
from dev_bot.pipeline import Pipeline
from dev_bot.repo import GitError, RepoManager
from dev_bot.state import State

REVIEW = json.dumps({
    "ready": True,
    "questions": [],
    "contracts": [
        {"name": "UploadedMedia", "owner": "ELE-1", "used_by": ["ELE-2"], "definition": "{ publicUrl: string }"},
        {"name": "HeroProps", "owner": "ELE-3", "used_by": [], "definition": "{ banner: string }"},
    ],
    "conflicts": [{"stories": ["ELE-2", "ELE-3"], "detail": "two names for one field", "resolution": "use publicUrl"}],
    "oversized": [],
})


class EpicRunJira(FakeJira):
    def __init__(self, epic, stories):
        super().__init__()
        self.epic, self.stories = epic, stories

    def get_issue(self, key):
        return self.epic

    def children(self, key):
        return self.stories


class StoryEngine(FakeEngine):
    """Implements each ticket by writing its own file, and records what the worktree held when it started."""

    def __init__(self, fail_for=(), **kw):
        super().__init__(epic_plan=REVIEW, **kw)
        self.fail_for = set(fail_for)
        self.seen: dict[str, list[str]] = {}
        self.story_prompts: dict[str, str] = {}

    def run(self, workdir, prompt, timeout_s):
        m = re.match(r"You are implementing Jira ticket (\S+) in", prompt)
        if not m:
            return super().run(workdir, prompt, timeout_s)
        key = m.group(1)
        self.prompts.append(prompt)
        self.story_prompts[key] = prompt
        self.seen[key] = sorted(p.name for p in (workdir / "src").iterdir())
        if key in self.fail_for:
            return EngineResult(ok=False, error="credits exhausted")
        (workdir / "src" / f"{key}.txt").write_text(f"{key}\n", encoding="utf-8")
        return EngineResult(ok=True, response="Done.")


def three_stories():
    """ELE-1 <- ELE-2 <- ELE-3, one chain."""
    return [
        story("ELE-1", "backend", "src/api/a.ts"),
        story("ELE-2", "backend", "src/api/b.ts", blockers=blocked_by("ELE-1")),
        story("ELE-3", "frontend", "src/ui/c.tsx", blockers=blocked_by("ELE-2")),
    ]


def five_stories():
    """ELE-3 hangs off a failing ELE-2; ELE-4 depends on nothing; ELE-5 needs both ELE-3 and ELE-4."""
    return [
        story("ELE-1", "backend", "src/api/a.ts"),
        story("ELE-2", "backend", "src/api/b.ts", blockers=blocked_by("ELE-1")),
        story("ELE-3", "backend", "src/api/c.ts", blockers=blocked_by("ELE-2")),
        story("ELE-4", "frontend", "src/ui/d.tsx"),
        story("ELE-5", "frontend", "src/ui/e.tsx", blockers=blocked_by("ELE-3", "ELE-4")),
    ]


@pytest.fixture
def make(tmp_path, origin, cfg):
    def _make(stories, engine=None, dry_run=False):
        epic = make_issue("ELE-100", "[Epic] Image upload", "ELE", "The whole thing", issue_type="Epic")
        jira, gh = EpicRunJira(epic, stories), FakeGitHub()
        engine = engine or StoryEngine()
        repos = RepoManager(cfg.work_dir, remote_url=lambda name: str(origin))
        pipeline = Pipeline(cfg, jira, repos, gh, engine, State(cfg.work_dir / "s.sqlite"), Notifier(), dry_run)
        return pipeline, jira, gh, engine

    return _make


def branch(pipeline, stories, key):
    return pipeline.branch_for(next(s for s in stories if s.key == key))


def statuses(run):
    return [(r.key, r.status) for r in run.results]


# -- the stack ---------------------------------------------------------------------------------------
def test_each_story_is_built_on_the_one_before_it(make, origin):
    stories = three_stories()
    pipeline, jira, gh, engine = make(stories)

    run = run_epic(pipeline, "ELE-100")

    assert statuses(run) == [("ELE-1", "pr_opened"), ("ELE-2", "pr_opened"), ("ELE-3", "pr_opened")]
    b1, b2, b3 = (branch(pipeline, stories, k) for k in ("ELE-1", "ELE-2", "ELE-3"))
    assert [(pr["branch"], pr["base"]) for pr in gh.opened] == [(b1, "main"), (b2, b1), (b3, b2)]
    # Each story saw the code of the stories under it, and its branch adds exactly one commit to theirs.
    assert engine.seen["ELE-1"] == ["app.txt"]
    assert engine.seen["ELE-2"] == ["ELE-1.txt", "app.txt"]
    assert engine.seen["ELE-3"] == ["ELE-1.txt", "ELE-2.txt", "app.txt"]
    assert git("rev-list", "--count", f"main..{b1}", cwd=origin).strip() == "1"
    assert git("rev-list", "--count", f"{b1}..{b2}", cwd=origin).strip() == "1"
    assert git("rev-list", "--count", f"{b2}..{b3}", cwd=origin).strip() == "1"
    assert git("show", f"{b3}:src/ELE-3.txt", cwd=origin) == "ELE-3\n"


def test_a_blocker_inside_the_epic_does_not_hold_back_its_story(make):
    # ELE-1 is still "To Do" in Jira while its PR is open, but ELE-2 is built on top of it.
    pipeline, jira, gh, engine = make(three_stories())
    run_epic(pipeline, "ELE-100")
    assert not any(c[0] == "comment" and "blocked" in c[2].lower() for c in jira.calls)
    assert len(gh.opened) == 3


def test_a_blocker_outside_the_epic_still_blocks(make):
    stories = three_stories()
    stories[0] = story("ELE-1", "backend", "src/api/a.ts", blockers=blocked_by("OPS-7"))
    pipeline, jira, gh, engine = make(stories)

    run = run_epic(pipeline, "ELE-100")

    assert statuses(run) == [("ELE-1", "skipped"), ("ELE-2", "skipped"), ("ELE-3", "skipped")]
    assert "OPS-7" in run.results[0].detail and "depends on ELE-1 (skipped)" in run.results[1].detail
    assert gh.opened == []


def test_the_engine_is_told_what_the_story_must_agree_on(make):
    pipeline, jira, gh, engine = make(three_stories())
    run_epic(pipeline, "ELE-100")

    second = engine.story_prompts["ELE-2"]
    assert "is story 2 of 3 in Epic ELE-100" in second
    assert "- ELE-1: BE: ELE-1" in second  # already in the branch
    assert "- ELE-3: FE: ELE-3" in second  # built after it
    assert "UploadedMedia (owner ELE-1; used by ELE-2): { publicUrl: string }" in second
    assert "HeroProps" not in second  # a contract between other stories is left out
    assert "two names for one field Resolution: use publicUrl" in second
    assert "UploadedMedia" not in engine.story_prompts["ELE-3"]
    assert "HeroProps" in engine.story_prompts["ELE-3"]
    assert second.startswith("You are implementing Jira ticket ELE-2")  # the context is added, not put first


def test_the_readiness_check_sees_what_the_epic_review_already_decided(make):
    pipeline, jira, gh, engine = make(three_stories())
    run_epic(pipeline, "ELE-100")

    check = next(p for p in engine.prompts if p.startswith("READINESS CHECK for Jira ticket ELE-2"))
    assert "UploadedMedia (owner ELE-1; used by ELE-2)" in check
    assert "two names for one field Resolution: use publicUrl" in check
    assert "do not ask about anything the contracts and resolutions above decide" in check


def test_a_ticket_outside_an_epic_is_checked_on_its_own_text(build):
    pipeline, jira, gh, repos = build(FakeEngine())
    pipeline.process(make_issue())
    check = next(p for p in pipeline.engine.prompts if p.startswith("READINESS CHECK"))
    assert "Epic" not in check


def test_the_pr_says_where_it_sits_in_the_stack(make):
    stories = three_stories()
    pipeline, jira, gh, engine = make(stories)
    run_epic(pipeline, "ELE-100")

    b1 = branch(pipeline, stories, "ELE-1")
    assert "story 1 of 3 in Epic [ELE-100](https://x.atlassian.net/browse/ELE-100)" in gh.opened[0]["body"]
    assert "based on `main`" in gh.opened[0]["body"]
    assert f"based on `{b1}` (ELE-1), so merge the stories from the bottom of the stack up" in gh.opened[1]["body"]


def test_commits_are_one_per_story_even_when_the_pr_is_stacked(make, origin):
    stories = three_stories()
    pipeline, jira, gh, engine = make(stories)
    run_epic(pipeline, "ELE-100")
    assert git("log", "-1", "--format=%s", branch(pipeline, stories, "ELE-2"), cwd=origin).strip() == "ELE-2: BE: ELE-2"


# -- a story goes wrong ------------------------------------------------------------------------------
def test_a_failed_story_skips_only_what_depends_on_it(make):
    stories = five_stories()
    pipeline, jira, gh, engine = make(stories, StoryEngine(fail_for={"ELE-2"}))

    run = run_epic(pipeline, "ELE-100")

    assert statuses(run) == [
        ("ELE-1", "pr_opened"), ("ELE-2", "failed"), ("ELE-3", "skipped"), ("ELE-4", "pr_opened"), ("ELE-5", "skipped"),
    ]
    assert run.failed
    assert "depends on ELE-2 (failed)" in run.results[2].detail
    assert "depends on ELE-3 (skipped)" in run.results[4].detail
    # ELE-4 does not depend on ELE-2, so it is built on the last story that did get built.
    b1 = branch(pipeline, stories, "ELE-1")
    assert [(pr["branch"], pr["base"]) for pr in gh.opened] == [
        (b1, "main"), (branch(pipeline, stories, "ELE-4"), b1),
    ]
    assert "ELE-3" not in engine.story_prompts and "ELE-5" not in engine.story_prompts
    assert all(c[1] != "ELE-3" for c in jira.calls)  # a skipped story is left alone in Jira


def test_a_story_handed_back_with_questions_is_skipped_around_like_a_failure(make):
    stories = three_stories()
    engine = StoryEngine(ready=False)  # every readiness check bounces the ticket
    pipeline, jira, gh, engine = make(stories, engine)
    run = run_epic(pipeline, "ELE-100")
    assert statuses(run) == [("ELE-1", "needs_info"), ("ELE-2", "skipped"), ("ELE-3", "skipped")]
    assert not run.failed and gh.opened == []


def test_running_it_again_picks_up_where_it_stopped(make, origin):
    stories = five_stories()
    engine = StoryEngine(fail_for={"ELE-2"})
    pipeline, jira, gh, engine = make(stories, engine)
    run_epic(pipeline, "ELE-100")
    engine.fail_for.clear()  # the ticket was fixed
    before = len(engine.prompts)

    run = run_epic(pipeline, "ELE-100")

    # ELE-1 and ELE-4 already have PRs and are left alone; ELE-2 and ELE-3 are now built on ELE-1.
    assert statuses(run) == [
        ("ELE-1", "existing"), ("ELE-2", "pr_opened"), ("ELE-3", "pr_opened"), ("ELE-4", "existing"), ("ELE-5", "skipped"),
    ]
    b = {k: branch(pipeline, stories, k) for k in ("ELE-1", "ELE-2", "ELE-3")}
    assert [(pr["branch"], pr["base"]) for pr in gh.opened[2:]] == [(b["ELE-2"], b["ELE-1"]), (b["ELE-3"], b["ELE-2"])]
    # ELE-4's PR sits beside ELE-3, not under it, so ELE-5 (which needs both) is held for a person to rebase.
    assert "ELE-4 has a PR that is not under" in run.results[4].detail
    assert not any(p.startswith("EPIC PLAN") for p in engine.prompts[before:])  # the saved review was reused
    assert "ELE-1" not in {m.group(1) for p in engine.prompts[before:] if (m := re.match(r"You are implementing Jira ticket (\S+)", p))}


def test_the_review_is_kept_so_every_run_uses_the_same_contracts(make):
    pipeline, jira, gh, engine = make(three_stories())
    run_epic(pipeline, "ELE-100")
    saved = pipeline.state.load_epic_plan("ELE-100")
    assert saved["contracts"][0]["name"] == "UploadedMedia"

    engine.epic_plan = REVIEW.replace("UploadedMedia", "MediaResult")
    run_epic(pipeline, "ELE-100")
    assert pipeline.state.load_epic_plan("ELE-100")["contracts"][0]["name"] == "UploadedMedia"
    run_epic(pipeline, "ELE-100", replan=True)
    assert pipeline.state.load_epic_plan("ELE-100")["contracts"][0]["name"] == "MediaResult"


# -- when nothing should be built --------------------------------------------------------------------
def test_an_epic_that_is_not_ready_builds_nothing(make):
    engine = StoryEngine()
    engine.epic_plan = '{"ready": false, "questions": ["ELE-2: which field?"]}'
    pipeline, jira, gh, engine = make(three_stories(), engine)

    run = run_epic(pipeline, "ELE-100")

    assert run.report.blocking and run.results == []
    assert gh.opened == [] and jira.calls == []
    assert pipeline.state.load_epic_plan("ELE-100") is None  # a review that said "not ready" is not kept


def test_without_the_engine_review_the_stories_are_still_stacked(make):
    pipeline, jira, gh, engine = make(three_stories())
    run = run_epic(pipeline, "ELE-100", use_engine=False)
    assert statuses(run) == [("ELE-1", "pr_opened"), ("ELE-2", "pr_opened"), ("ELE-3", "pr_opened")]
    assert not any(p.startswith("EPIC PLAN") for p in engine.prompts)
    assert "Shared contracts" not in engine.story_prompts["ELE-2"]
    assert "is story 2 of 3" in engine.story_prompts["ELE-2"]


def test_a_dry_run_pushes_nothing_and_opens_no_pr(make, origin):
    pipeline, jira, gh, engine = make(three_stories(), dry_run=True)

    run = run_epic(pipeline, "ELE-100")

    assert statuses(run) == [("ELE-1", "dry_run_ok"), ("ELE-2", "dry_run_ok"), ("ELE-3", "dry_run_ok")]
    assert gh.opened == [] and git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=origin).split() == ["main"]
    assert pipeline.state.load_epic_plan("ELE-100") is None  # a dry run leaves no trace
    assert "Dry run: nothing was pushed" in format_run(run)


def test_the_daily_cap_defers_the_rest(make, cfg):
    cfg.daily_cap = 1
    pipeline, jira, gh, engine = make(three_stories())
    run = run_epic(pipeline, "ELE-100")
    assert statuses(run) == [("ELE-1", "pr_opened"), ("ELE-2", "deferred"), ("ELE-3", "deferred")]
    assert "daily ticket cap" in run.results[1].detail and not run.failed


def test_a_paused_bot_does_nothing(make, cfg):
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.paused_file.touch()
    pipeline, jira, gh, engine = make(three_stories())
    run = run_epic(pipeline, "ELE-100")
    assert run.halted == "paused" and run.report is None and engine.prompts == [] and jira.calls == []


def test_the_summary_lists_every_story(make):
    pipeline, jira, gh, engine = make(five_stories(), StoryEngine(fail_for={"ELE-2"}))
    text = format_run(run_epic(pipeline, "ELE-100"))
    lines = text.splitlines()
    assert lines[0] == "dev-bot epic ELE-100: 2 of 5 stories built"
    assert lines[1] == "  1. ELE-1: pr_opened https://github.com/acme/app/pull/1"
    assert lines[2].startswith("  2. ELE-2: failed The coding engine failed")
    assert lines[3] == "  3. ELE-3: skipped depends on ELE-2 (failed)"


# -- the pieces --------------------------------------------------------------------------------------
def test_a_worktree_can_only_stack_on_a_bot_branch(cfg, origin, repo_cfg):
    repos = RepoManager(cfg.work_dir, remote_url=lambda name: str(origin))
    with pytest.raises(GitError, match="non-bot branch"):
        repos.create_worktree(repo_cfg, "bot/ELE-1-x", "some-feature")


def test_is_ancestor_tells_stacked_branches_from_siblings(make, origin, repo_cfg):
    stories = five_stories()
    pipeline, jira, gh, engine = make(stories, StoryEngine(fail_for={"ELE-2"}))
    run_epic(pipeline, "ELE-100")
    b1, b4 = branch(pipeline, stories, "ELE-1"), branch(pipeline, stories, "ELE-4")
    assert pipeline.repos.is_ancestor(repo_cfg, b1, b4)
    assert not pipeline.repos.is_ancestor(repo_cfg, b4, b1)


def test_a_saved_review_round_trips():
    verdict = EpicVerdict(True, [], [{"name": "A"}], [{"stories": ["ELE-1"]}], [])
    assert EpicVerdict.from_dict(json.loads(json.dumps(verdict.to_dict()))) == verdict


def test_the_epic_command_runs_the_epic_unless_asked_only_to_plan(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_epic_command", lambda *a, **kw: calls.append(("run", a, kw)) or 0)
    monkeypatch.setattr(cli, "plan_epic_command", lambda *a, **kw: calls.append(("plan", a, kw)) or 0)

    assert cli.main(["--dry-run", "epic", "ELE-100", "--no-engine", "--replan"]) == 0
    assert cli.main(["epic", "ELE-100", "--plan"]) == 0

    (_, run_args, run_kw), (_, plan_args, plan_kw) = calls
    assert run_args[1:3] == ("ELE-100", True) and run_kw == {"use_engine": False, "replan": True}
    assert plan_args[1] == "ELE-100" and plan_kw == {"use_engine": True}
