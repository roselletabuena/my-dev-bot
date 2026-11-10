from __future__ import annotations

from conftest import FakeEngine, git, make_issue
from dev_bot.jira import Blocker


def test_blocked_ticket_is_skipped_without_claim_and_commented(build):
    engine = FakeEngine()
    pipeline, jira, gh, _ = build(engine)
    blocker = Blocker(key="ELE-2", summary="Prereq", status="In Progress", resolved=False)
    outcome = pipeline.process(make_issue(blockers=[blocker]))

    assert outcome.status == "skipped"
    assert "ELE-2" in outcome.detail
    assert not engine.prompts  # never touched the engine
    assert not gh.opened
    assert any(c[0] == "comment" and c[1] == "ELE-1" for c in jira.calls)
    assert not any(c[0] in ("add_label", "remove_label", "transition") for c in jira.calls)


def test_resolved_blocker_does_not_block(build):
    engine = FakeEngine()
    pipeline, jira, gh, _ = build(engine)
    blocker = Blocker(key="ELE-2", summary="Prereq", status="Done", resolved=True)
    outcome = pipeline.process(make_issue(blockers=[blocker]))

    assert outcome.status == "pr_opened"


def test_happy_path_opens_draft_pr_and_pushes_bot_branch(build, origin, cfg):
    pipeline, jira, gh, repos = build(FakeEngine())
    outcome = pipeline.process(make_issue())

    assert outcome.status == "pr_opened"
    assert outcome.pr_url == "https://github.com/acme/app/pull/1"
    assert gh.opened[0]["branch"] == "bot/ELE-1-fix-greeting"
    assert "ELE-1" in gh.opened[0]["body"]
    assert ("add_label", "ELE-1", "bot-claimed") in jira.calls
    assert ("transition", "ELE-1", "In Review") in jira.calls
    # the branch really landed on the remote with the engine's change
    assert git("show", "bot/ELE-1-fix-greeting:src/app.txt", cwd=origin) == "hello electa\n"
    # the worktree was cleaned up
    assert not any(repos.worktrees_dir.iterdir())
    assert pipeline.state.count_today() == 1


def test_vague_ticket_is_bounced_with_questions_and_not_coded(build):
    engine = FakeEngine(ready=False)
    pipeline, jira, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "needs_info"
    assert "Which page?" in outcome.detail
    assert ("remove_label", "ELE-1", "bot-claimed") in jira.calls
    assert ("transition", "ELE-1", "Needs Info") in jira.calls
    assert not gh.opened
    assert len(engine.prompts) == 1  # only the readiness check ran


def test_no_diff_fails_and_keeps_claim(build):
    pipeline, jira, gh, _ = build(FakeEngine(edits={}))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "no changes" in outcome.detail
    assert ("remove_label", "ELE-1", "bot-claimed") not in jira.calls
    assert not gh.opened


def test_protected_path_is_rejected(build):
    pipeline, _, gh, _ = build(FakeEngine(edits={".github/workflows/ci.yml": "x\n"}))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "protected path" in outcome.detail
    assert not gh.opened


def test_oversized_diff_is_rejected(build):
    pipeline, _, gh, _ = build(FakeEngine(edits={"src/big.txt": "line\n" * 200}))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "limit" in outcome.detail
    assert not gh.opened


def test_failing_check_blocks_the_pr(build, repo_cfg):
    repo_cfg.check_commands = ['python -c "import sys; sys.exit(1)"']
    pipeline, _, gh, _ = build(FakeEngine())
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "failed" in outcome.detail
    assert not gh.opened


def test_engine_failure_is_reported(build):
    pipeline, jira, gh, _ = build(FakeEngine(fail=True))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert any(c[0] == "comment" and "could not complete" in c[2] for c in jira.calls)
    assert not gh.opened


def test_dry_run_writes_nothing_external(build, origin):
    pipeline, jira, gh, _ = build(FakeEngine(), dry_run=True)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "dry_run_ok"
    assert jira.calls == []
    assert not gh.opened
    assert git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=origin).split() == ["main"]  # nothing pushed
    assert pipeline.state.count_today() == 0


def test_unmapped_project_is_skipped(build):
    pipeline, jira, _, _ = build(FakeEngine())
    outcome = pipeline.process(make_issue(project="OTHER"))

    assert outcome.status == "skipped"
    assert jira.calls == []


def test_kill_switch_stops_run(build, cfg):
    pipeline, jira, _, _ = build(FakeEngine())
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.paused_file.write_text("")

    assert pipeline.run_once() == []
    assert jira.calls == []


def test_daily_cap_stops_run(build, cfg):
    cfg.daily_cap = 1
    pipeline, jira, gh, _ = build(FakeEngine())
    first = pipeline.run_once()
    second = pipeline.run_once()

    assert [o.status for o in first] == ["pr_opened"]
    assert second == []
    assert len(gh.opened) == 1


# -- repair loop -------------------------------------------------------------------------------
# The check passes only once src/app.txt reads "fixed", so the engine's first attempt fails it.
NEEDS_FIXED = 'python -c "import sys; sys.exit(0 if open(\'src/app.txt\').read().strip()==\'fixed\' else 1)"'


def test_failed_check_is_repaired_and_pr_opens(build, repo_cfg):
    repo_cfg.check_commands = [NEEDS_FIXED]
    engine = FakeEngine(repair_edits={"src/app.txt": "fixed\n"})
    pipeline, jira, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "pr_opened"
    assert len(engine.repair_prompts) == 1
    assert "Failed command:" in engine.repair_prompts[0] and "ELE-1" in engine.repair_prompts[0]
    assert "after 1 automatic fix round(s)" in gh.opened[0]["body"]


def test_repair_gives_up_after_configured_rounds(build, cfg, repo_cfg):
    repo_cfg.check_commands = [NEEDS_FIXED]
    cfg.repair_rounds = 2
    engine = FakeEngine()  # repairs change nothing, so the check never passes
    pipeline, _, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "after 2 repair attempt(s)" in outcome.detail
    assert len(engine.repair_prompts) == 2
    assert not gh.opened


def test_repair_disabled_fails_immediately(build, cfg, repo_cfg):
    repo_cfg.check_commands = [NEEDS_FIXED]
    cfg.repair_rounds = 0
    engine = FakeEngine(repair_edits={"src/app.txt": "fixed\n"})
    pipeline, _, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert engine.repair_prompts == []
    assert not gh.opened


def test_repair_touching_protected_path_is_rejected(build, repo_cfg):
    repo_cfg.check_commands = [NEEDS_FIXED]
    engine = FakeEngine(repair_edits={".github/workflows/ci.yml": "x\n"})
    pipeline, _, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "repaired change" in outcome.detail and "protected path" in outcome.detail
    assert not gh.opened


def test_engine_failure_during_repair_is_reported(build, repo_cfg):
    repo_cfg.check_commands = [NEEDS_FIXED]
    pipeline, _, gh, _ = build(FakeEngine(repair_fail=True))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed"
    assert "during repair" in outcome.detail
    assert not gh.opened


def test_setup_failure_is_not_sent_to_the_engine_for_repair(build, repo_cfg):
    repo_cfg.setup_commands = ['python -c "import sys; sys.exit(1)"']
    engine = FakeEngine()
    pipeline, _, _, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "failed" and "Setup" in outcome.detail
    assert engine.repair_prompts == []


def test_task_prompt_lists_the_checks(build, repo_cfg):
    engine = FakeEngine()
    pipeline, _, _, _ = build(engine)
    pipeline.process(make_issue())

    implement_prompt = next(p for p in engine.prompts if p.startswith("You are implementing"))
    assert repo_cfg.check_commands[0] in implement_prompt


# -- ordering and engine-side commits ----------------------------------------------------------
def test_setup_runs_before_the_engine(build, repo_cfg, tmp_path):
    marker = tmp_path / "setup-marker.txt"
    repo_cfg.setup_commands = [f"python -c \"open(r'{marker.as_posix()}', 'w').write('1')\""]
    engine = FakeEngine(marker=marker)
    pipeline, _, _, _ = build(engine)
    outcome = pipeline.process(make_issue())

    assert outcome.status == "pr_opened"
    assert engine.marker_seen is True


def test_engine_that_commits_itself_is_squashed_into_one_bot_commit(build, origin):
    pipeline, _, gh, _ = build(FakeEngine(commits=True))
    outcome = pipeline.process(make_issue())

    assert outcome.status == "pr_opened"  # not a false "no changes"
    log = git("log", "--format=%s", "main..bot/ELE-1-fix-greeting", cwd=origin).splitlines()
    assert log == ["ELE-1: Fix greeting"]  # agent's commit squashed, bot's message used
    assert git("show", "bot/ELE-1-fix-greeting:src/app.txt", cwd=origin) == "hello electa\n"


def test_worktree_checkout_keeps_lf_line_endings(build, repo_cfg):
    """Windows git defaults to autocrlf=true; the bot must not turn an LF repo into CRLF."""
    _, _, _, repos = build(FakeEngine())
    worktree = repos.create_worktree(repo_cfg, "bot/ELE-9-lf-check")
    try:
        assert b"\r" not in (worktree / "src" / "app.txt").read_bytes()
    finally:
        repos.remove_worktree(repo_cfg, worktree)
