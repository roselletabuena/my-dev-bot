from __future__ import annotations

import time

import pytest

from conftest import FakeEngine, make_issue
from dev_bot.config import RepoConfig
from dev_bot.engine.base import scrubbed_env
from dev_bot.guardrails import check_changes
from dev_bot.jira import JiraClient, adf_to_text
from dev_bot.lock import LockHeld, RunLock
from dev_bot.repo import Changes, slugify
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


def test_slugify():
    assert slugify("Fix: the Login!! button") == "fix-the-login-button"
    assert slugify("???") == "change"


def test_extract_json_tolerates_noise():
    assert extract_json('Sure! {"ready": true, "questions": []} done') == {"ready": True, "questions": []}
    assert extract_json("no json here") is None


def test_triage_parse_failure_raises():
    class Garbage:
        def run(self, workdir, prompt, timeout_s):
            from dev_bot.engine.base import EngineResult
            return EngineResult(ok=True, response="I think it's fine")

    with pytest.raises(TriageError):
        check_readiness(Garbage(), make_issue(), 5)


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
                   "labels": ["electa-bot"],
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
