from __future__ import annotations

import logging
import time

import pytest

from conftest import FakeEngine, make_issue
from dev_bot.config import RepoConfig
from dev_bot.engine.base import scrubbed_env
from dev_bot.guardrails import check_changes
from dev_bot.jira import JiraClient, adf_to_text
from dev_bot.lock import LockHeld, RunLock
from dev_bot.repo import Changes, RepoManager, slugify
from dev_bot.triage import TriageError, check_readiness, extract_json


def repo(**kw) -> RepoConfig:
    return RepoConfig(name="a/b", allowed_paths=["src/"], denied_paths=[".github/", ".env*", "package-lock.json"], **kw)


def test_guardrails_paths_and_size():
    assert check_changes(Changes(["src/a.ts"], 10), repo()) == []
    assert check_changes(Changes([], 0), repo()) == ["The agent made no changes."]
    assert any("limit" in p for p in check_changes(Changes(["src/a.ts"], 999), repo()))
    assert any("protected" in p for p in check_changes(Changes([".github/ci.yml"], 1), repo()))
    assert any("protected" in p for p in check_changes(Changes(["src/.env.local"], 1), repo()))
    assert any("protected" in p for p in check_changes(Changes(["package-lock.json"], 1), repo()))
    assert any("Outside" in p for p in check_changes(Changes(["scripts/x.js"], 1), repo()))


def test_scrubbed_env_drops_credentials(monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "a")
    monkeypatch.setenv("GITHUB_TOKEN", "b")
    monkeypatch.setenv("GH_TOKEN", "c")
    monkeypatch.setenv("MY_SECRET", "d")
    monkeypatch.setenv("KEEP_ME", "ok")
    env = scrubbed_env({"CI": "1"})
    assert env["KEEP_ME"] == "ok" and env["CI"] == "1"
    assert not {"JIRA_API_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "MY_SECRET"} & set(env)


def test_adf_to_text():
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Line one"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Line two"}]},
    ]}
    assert adf_to_text(adf).strip() == "Line one\nLine two"
    assert adf_to_text(None) == ""


def test_adf_to_text_renders_figma_smart_link():
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Figma: ", "marks": [{"type": "strong"}]},
            {"type": "inlineCard", "attrs": {"url": "https://www.figma.com/design/abc?node-id=1-2"}},
        ]},
    ]}
    assert "https://www.figma.com/design/abc?node-id=1-2" in adf_to_text(adf)


def test_commits_are_unsigned_without_a_signing_key(tmp_path):
    assert RepoManager(tmp_path)._signing_key is None


def test_signing_key_is_picked_up_from_the_work_dir(tmp_path):
    key = tmp_path / "signing_key"
    key.write_text("not a real key, just needs to exist\n", encoding="utf-8")
    assert RepoManager(tmp_path)._signing_key == key


def _commit_message(tmp_path, **kw) -> str:
    import subprocess

    wt = tmp_path / "wt"
    wt.mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=wt, check=True, capture_output=True, text=True).stdout  # noqa: E731
    git("init", "-q")
    git("-c", "user.name=x", "-c", "user.email=x@x", "commit", "--allow-empty", "-q", "-m", "base")
    base = git("rev-parse", "HEAD").strip()
    (wt / "f.txt").write_text("hi", encoding="utf-8")
    git("add", "-A")
    RepoManager(tmp_path / "root", user_name="dev-bot", user_email="bot@x", **kw).commit(wt, "VS-1: Do thing", base)
    return git("log", "-1", "--format=%an <%ae>%n%B")


def test_commit_credits_the_co_author_when_configured(tmp_path):
    out = _commit_message(
        tmp_path, co_author_name="roselletabuena", co_author_email="31462246+roselletabuena@users.noreply.github.com"
    )
    assert out.startswith("dev-bot <bot@x>")
    assert "VS-1: Do thing\n\nCo-authored-by: roselletabuena <31462246+roselletabuena@users.noreply.github.com>" in out


def test_commit_has_no_trailer_without_a_co_author(tmp_path):
    assert "Co-authored-by" not in _commit_message(tmp_path)


def test_slugify():
    assert slugify("Fix: the Login!! button") == "fix-the-login-button"
    assert slugify("???") == "change"


def test_extract_json_tolerates_noise():
    assert extract_json('Sure! {"ready": true, "questions": []} done') == {"ready": True, "questions": []}
    assert extract_json("no json here") is None
    # Prioritizes dict with 'ready' if an earlier unrelated dict exists
    noisy = 'Here is config {"timeout": 30} and answer {"ready": false, "questions": ["why?"]}'
    assert extract_json(noisy) == {"ready": False, "questions": ["why?"]}


def test_triage_parse_failure_raises():
    class Garbage:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(ok=True, response="I think it's fine")

    with pytest.raises(TriageError):
        check_readiness(Garbage(), make_issue(), 5)


def test_triage_markdown_fallback():
    class MarkdownReady:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(
                ok=True,
                response=(
                    "# Readiness Check: Jira Ticket [AD-14]\n\n"
                    "### **Summary:** Implement Calendar Button\n\n"
                    '### **Readiness Verdict:** <span style="color:green; font-weight:bold;">READY</span>'
                ),
            )

    class MarkdownNotReady:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(
                ok=True,
                response=(
                    "# Readiness Check\n"
                    "### Readiness Verdict: NOT READY\n"
                    "- Where should this file live?\n"
                    "- What are the test expectations?"
                ),
            )

    r_ready = check_readiness(MarkdownReady(), make_issue(), 5)
    assert r_ready.ready and not r_ready.questions

    r_not_ready = check_readiness(MarkdownNotReady(), make_issue(), 5)
    assert not r_not_ready.ready
    assert len(r_not_ready.questions) == 2
    assert "Where should this file live?" in r_not_ready.questions[0]


def test_triage_ad4_style_markdown():
    """Reproduces the exact shape of engine output seen on AD-4 that crashed triage."""
    class AD4MarkdownReady:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(
                ok=True,
                response=(
                    "# Readiness Check: [AD-4](https://the-three-devsketeers.atlassian.net/browse/AD-4) — Implement Avatar Group component\n\n"
                    "---\n\n"
                    "### **1. Ticket Overview**\n"
                    "* **Issue Key**: [`AD-4`](https://the-three-devsketeers.atlassian.net/browse/AD-4)\n"
                    "* **Summary**: Implement Avatar Group component\n\n"
                    "### **2. Requirements & Acceptance Criteria**\n"
                    "- 5-file co-located structure under `src/components/data-display/AvatarGroup/`\n\n"
                    "### **3. Readiness Verdict**\n\n"
                    "**READY**\n"
                    "The ticket contains all necessary context and criteria to begin implementation."
                ),
            )

    class AD4MarkdownNotReady:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(
                ok=True,
                response=(
                    "# Readiness Check: [AD-4](https://the-three-devsketeers.atlassian.net/browse/AD-4) — Implement Avatar Group component\n\n"
                    "---\n\n"
                    "### **1. Ticket Overview**\n"
                    "* **Issue Key**: [`AD-4`](https://the-three-devsketeers.atlassian.net/browse/AD-4)\n\n"
                    "### **2. Missing Information**\n"
                    "1. The Figma link is missing from the ticket description.\n"
                    "2. What avatar sizes and variants should be supported?\n\n"
                    "### **3. Readiness Verdict**\n\n"
                    "🔴 **NOT READY**\n"
                ),
            )

    r_ready = check_readiness(AD4MarkdownReady(), make_issue(), 5)
    assert r_ready.ready and not r_ready.questions

    r_not_ready = check_readiness(AD4MarkdownNotReady(), make_issue(), 5)
    assert not r_not_ready.ready
    assert len(r_not_ready.questions) == 2
    assert "Figma link is missing" in r_not_ready.questions[0]
    assert "sizes and variants" in r_not_ready.questions[1]


def test_triage_not_ready_always_has_a_question():
    class Vague:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(ok=True, response='{"ready": false}')

    r = check_readiness(Vague(), make_issue(), 5)
    assert not r.ready and r.questions


def test_triage_ready_with_fake_engine():
    assert check_readiness(FakeEngine(), make_issue(), 5).ready


def test_run_lock_blocks_second_holder(tmp_path):
    path = tmp_path / ".run.lock"
    with RunLock(path):
        with pytest.raises(LockHeld):
            with RunLock(path):
                pass
    assert not path.exists()


def test_run_lock_breaks_stale_lock(tmp_path):
    path = tmp_path / ".run.lock"
    path.write_text("999")
    old = time.time() - 10_000
    import os
    os.utime(path, (old, old))
    with RunLock(path, stale_after_s=60):
        assert path.exists()


def test_jira_search_transition_and_comment(requests_mock):
    base = "https://x.atlassian.net/rest/api/3"
    requests_mock.post(f"{base}/search/jql", json={"issues": [{
        "key": "ELE-9",
        "fields": {"summary": "S", "project": {"key": "ELE"}, "status": {"name": "Ready for Bot"},
                   "labels": ["dev-bot"],
                   "description": {"type": "doc", "content": [
                       {"type": "paragraph", "content": [{"type": "text", "text": "Do it"}]}]}},
    }]})
    requests_mock.get(f"{base}/issue/ELE-9/transitions", json={"transitions": [{"id": "31", "name": "Start", "to": {"name": "In Progress"}}]})
    post_t = requests_mock.post(f"{base}/issue/ELE-9/transitions", status_code=204)
    comment = requests_mock.post(f"{base}/issue/ELE-9/comment", json={})

    client = JiraClient("https://x.atlassian.net", "me@x.com", "tok")
    [issue] = client.search("project = ELE")
    assert (issue.key, issue.project, issue.description) == ("ELE-9", "ELE", "Do it")
    assert client.transition("ELE-9", "In Progress") is True  # matched by target status name
    assert post_t.last_request.json() == {"transition": {"id": "31"}}
    assert client.transition("ELE-9", "Nonexistent") is False
    client.comment("ELE-9", "hi")
    assert comment.last_request.headers["Authorization"].startswith("Basic ")
    assert comment.last_request.json()["body"]["content"][0]["content"][0]["text"] == "hi"


def test_a_missing_jira_transition_says_what_the_workflow_does_offer(requests_mock, caplog):
    base = "https://x.atlassian.net/rest/api/3/issue/AD-1/transitions"
    requests_mock.get(base, json={"transitions": [{"id": "5", "name": "Review", "to": {"name": "For Review"}}]})
    client = JiraClient("https://x.atlassian.net", "me@x.com", "tok")
    with caplog.at_level(logging.WARNING, logger="dev_bot"):
        assert client.transition("AD-1", "In Review") is False  # this project has no "In Review" status
    assert "'Review' -> 'For Review'" in caplog.text and "'In Review'" in caplog.text

    post = requests_mock.post(base, status_code=204)
    assert client.transition("AD-1", "For Review") is True  # matched by the target status, not the transition's name
    assert post.last_request.json() == {"transition": {"id": "5"}}


def test_github_open_draft_pr_converts_existing_to_draft(monkeypatch):
    from dev_bot.github import GitHubClient, PullRequest

    client = GitHubClient("fake-token")
    monkeypatch.setattr(client, "find_open_pr", lambda repo, branch: PullRequest(42, "https://github.com/o/r/pull/42"))
    converted = []
    monkeypatch.setattr(client, "convert_to_draft", lambda repo, num: converted.append((repo, num)))
    pr = client.open_draft_pr("o/r", "bot/x", "main", "title", "body")
    assert pr.number == 42
    assert converted == [("o/r", 42)]


def test_rmtree_retries_transient_permission_error(tmp_path, monkeypatch):
    from dev_bot import repo as repo_mod

    target = tmp_path / "wt"
    target.mkdir()
    (target / "file.txt").write_text("hi", encoding="utf-8")

    real_rmtree = repo_mod.shutil.rmtree
    attempts = 0

    def flaky_rmtree(p, onerror=None):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(32, "The process cannot access the file")
        return real_rmtree(p, onerror=onerror)

    monkeypatch.setattr(repo_mod.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(repo_mod.time, "sleep", lambda _s: None)

    repo_mod._rmtree(target)
    assert attempts == 3
    assert not target.exists()


def test_remove_worktree_warns_instead_of_raising_on_persistent_lock(tmp_path, monkeypatch, caplog):
    from dev_bot import repo as repo_mod
    from dev_bot.config import RepoConfig
    from dev_bot.repo import RepoManager

    wt = tmp_path / "worktrees" / "bot-vs-1"
    wt.mkdir(parents=True)
    repos = RepoManager(tmp_path)
    cfg = RepoConfig(name="o/r", jira_projects=["VS"])

    monkeypatch.setattr(repos, "_git", lambda *a, **kw: "")
    monkeypatch.setattr(repo_mod, "_rmtree", lambda _p: (_ for _ in ()).throw(PermissionError(32, "locked")))

    with caplog.at_level(logging.WARNING, logger="dev_bot"):
        repos.remove_worktree(cfg, wt)  # must not raise and clobber the ticket outcome

    assert "could not remove worktree" in caplog.text


def test_prune_stale_warns_instead_of_raising_on_persistent_lock(tmp_path, monkeypatch, caplog):
    from dev_bot import repo as repo_mod
    from dev_bot.repo import RepoManager

    repos = RepoManager(tmp_path)
    stale = repos.worktrees_dir / "bot-vs-1"
    stale.mkdir(parents=True)

    monkeypatch.setattr(repo_mod, "_rmtree", lambda _p: (_ for _ in ()).throw(PermissionError(32, "locked")))

    with caplog.at_level(logging.WARNING, logger="dev_bot"):
        repos.prune_stale()

    assert "could not prune stale worktree" in caplog.text


def test_antigravity_engine_sends_prompt_via_stream_json_stdin(tmp_path, monkeypatch):
    import json
    import subprocess
    from dev_bot.engine import antigravity as agy_mod

    seen = {}

    def fake_run_cli(cmd, workdir, timeout_s, stdin_text=None):
        seen["cmd"] = cmd
        seen["stdin"] = stdin_text
        stdout = (
            '{"event":"init","conversation_id":"c1"}\n'
            '{"event":"result","result":{"status":"SUCCESS","response":"READY","duration_seconds":1.5}}\n'
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(agy_mod, "run_cli", fake_run_cli)
    long_prompt = "x" * 20000
    res = agy_mod.AntigravityEngine().run(tmp_path, long_prompt, 60)

    assert res.ok is True
    assert res.response == "READY"
    assert res.duration_s == 1.5
    assert "-p" not in seen["cmd"]
    assert "--input-format" in seen["cmd"]
    assert json.loads(seen["stdin"]) == {
        "event": "user",
        "message": {"role": "user", "content": long_prompt},
    }
