from __future__ import annotations

import json

from conftest import FakeEngine, git, make_issue
from dev_bot.atomic_commit import CommitGroup, parse_commit_plan, plan_atomic_commits


def test_parse_commit_plan_normalizes_paths_and_prefixes_key():
    raw = """```json
    {"commits": [
      {"message": "feat(api): add handler", "files": ["./src/api.ts"]},
      {"message": "ELE-1: test: add unit tests", "files": ["src\\\\api.test.ts"]}
    ]}
    ```"""
    groups = parse_commit_plan(raw, "ELE-1", ["src/api.ts", "src/api.test.ts"])
    assert groups == [
        CommitGroup("ELE-1: feat(api): add handler", ["src/api.ts"]),
        CommitGroup("ELE-1: test: add unit tests", ["src/api.test.ts"]),
    ]


def test_parse_commit_plan_ignores_unknown_dedupes_and_appends_remaining():
    raw = json.dumps({
        "commits": [
            {"message": "feat: first", "files": ["src/a.ts", "src/invented.ts"]},
            {"message": "feat: second", "files": ["src/a.ts", "src/b.ts"]},
        ]
    })
    groups = parse_commit_plan(raw, "ELE-1", ["src/a.ts", "src/b.ts", "src/c.ts"])
    assert groups == [
        CommitGroup("ELE-1: feat: first", ["src/a.ts"]),
        CommitGroup("ELE-1: feat: second", ["src/b.ts"]),
        CommitGroup("ELE-1: chore: remaining changes", ["src/c.ts"]),
    ]


def test_parse_commit_plan_returns_none_on_invalid_or_empty():
    assert parse_commit_plan("not json", "ELE-1", ["src/a.ts", "src/b.ts"]) is None
    assert parse_commit_plan('{"commits": [{"message": "x", "files": ["nope.ts"]}]}', "ELE-1", ["src/a.ts", "src/b.ts"]) is None


def test_single_file_skips_engine_call():
    engine = FakeEngine()
    assert plan_atomic_commits(engine, make_issue(), ["src/app.txt"], "diff", 10) is None
    assert engine.prompts == []


def test_pipeline_creates_multiple_atomic_commits_for_multi_file_changes(build, origin):
    plan = json.dumps({
        "commits": [
            {"message": "feat: update app", "files": ["src/app.txt"]},
            {"message": "feat: add helper", "files": ["src/helper.txt"]},
        ]
    })
    engine = FakeEngine(
        edits={"src/app.txt": "hello electa\n", "src/helper.txt": "helper\n"},
        commit_plan=plan,
    )
    pipeline, _, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())
    assert outcome.status == "pr_opened"
    assert len(gh.opened) == 1

    subjects = git("log", "main..bot/ELE-1-fix-greeting", "--reverse", "--format=%s", cwd=origin).splitlines()
    assert subjects == ["ELE-1: feat: update app", "ELE-1: feat: add helper"]

    shas = git("log", "main..bot/ELE-1-fix-greeting", "--reverse", "--format=%H", cwd=origin).splitlines()
    assert git("diff-tree", "--no-commit-id", "--name-only", "-r", shas[0], cwd=origin).splitlines() == ["src/app.txt"]
    assert git("diff-tree", "--no-commit-id", "--name-only", "-r", shas[1], cwd=origin).splitlines() == ["src/helper.txt"]


def test_pipeline_falls_back_to_single_commit_when_atomic_plan_fails(build, origin):
    engine = FakeEngine(
        edits={"src/app.txt": "hello electa\n", "src/helper.txt": "helper\n"},
        commit_plan="fail",
    )
    pipeline, _, gh, _ = build(engine)
    outcome = pipeline.process(make_issue())
    assert outcome.status == "pr_opened"
    assert len(gh.opened) == 1

    subjects = git("log", "main..bot/ELE-1-fix-greeting", "--format=%s", cwd=origin).splitlines()
    assert subjects == ["ELE-1: Fix greeting"]
