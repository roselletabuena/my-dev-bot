"""Epic planning: ordering an Epic's stories into one stack, and the engine's review of the Epic."""

from __future__ import annotations

import pytest
from conftest import FakeEngine, make_issue

from dev_bot.epic import EpicPlanError, format_report, order_stories, plan_epic, review_epic
from dev_bot.jira import Blocker, JiraClient
from dev_bot.main import main
from dev_bot.prompt import build_epic_plan_prompt
from dev_bot.story import parse_story


def scope(*paths: str) -> str:
    return "🛠️ Technical Scope & Architecture\nTarget Files / Directories:\n" + "\n".join(paths) + "\n"


def blocked_by(*keys: str, resolved: bool = False) -> list[Blocker]:
    return [Blocker(k, "", "Done" if resolved else "To Do", resolved) for k in keys]


def story(key: str, side: str, *paths: str, blockers: list[Blocker] | None = None, done: bool = False):
    summary = f"{'BE' if side == 'backend' else 'FE'}: {key}"
    return make_issue(
        key, summary, "ELE", scope(*paths), blockers=blockers or [], labels=[side],
        status="Done" if done else "To Do", status_category="done" if done else "new",
    )


def order_of(*stories):
    issues = list(stories)
    return order_stories(issues, {s.key: parse_story(s) for s in issues})


def test_links_come_first_then_backend_before_frontend_then_key_order():
    order = order_of(
        story("ELE-5", "frontend", "src/ui/a.tsx", blockers=blocked_by("ELE-3")),
        story("ELE-4", "frontend", "src/ui/b.tsx"),
        story("ELE-3", "backend", "src/api/c.ts"),
        story("ELE-2", "backend", "src/api/d.ts"),
    )
    assert order.order == ["ELE-2", "ELE-3", "ELE-4", "ELE-5"]
    assert [(e.before, e.after, e.kind) for e in order.edges] == [("ELE-3", "ELE-5", "blocks")]


def test_a_link_can_put_a_frontend_story_before_a_backend_one():
    order = order_of(
        story("ELE-1", "backend", "src/api/a.ts", blockers=blocked_by("ELE-2")),
        story("ELE-2", "frontend", "src/ui/b.tsx"),
    )
    assert order.order == ["ELE-2", "ELE-1"]


def test_stories_sharing_a_file_keep_rank_order_even_when_a_link_would_let_the_later_one_go_first():
    # Without the overlap: ELE-11 and ELE-12 are free at the start, so ELE-11 would go first.
    order = order_of(
        story("ELE-10", "backend", "src/svc.ts", blockers=blocked_by("ELE-12")),
        story("ELE-11", "backend", "src/svc.ts"),
        story("ELE-12", "backend", "src/other.ts"),
    )
    assert order.order == ["ELE-12", "ELE-10", "ELE-11"]
    assert order.overlaps == [("ELE-10", "ELE-11", ["src/svc.ts"])]
    assert ("ELE-10", "ELE-11", "overlap") in [(e.before, e.after, e.kind) for e in order.edges]


def test_an_overlap_never_reverses_an_existing_link():
    order = order_of(
        story("ELE-1", "backend", "src/svc.ts", blockers=blocked_by("ELE-2")),
        story("ELE-2", "backend", "src/svc.ts"),
    )
    assert order.order == ["ELE-2", "ELE-1"]
    assert [e.kind for e in order.edges] == ["blocks"]  # the overlap adds no second, contradicting edge


def test_a_cycle_is_reported_instead_of_ordered():
    order = order_of(
        story("ELE-1", "backend", "src/a.ts", blockers=blocked_by("ELE-2")),
        story("ELE-2", "backend", "src/b.ts", blockers=blocked_by("ELE-1")),
    )
    assert order.order == []
    assert order.cycle in (["ELE-1", "ELE-2", "ELE-1"], ["ELE-2", "ELE-1", "ELE-2"])


def test_done_stories_are_left_out_but_still_order_the_rest():
    order = order_of(
        story("ELE-1", "backend", "src/a.ts", done=True),
        story("ELE-2", "frontend", "src/b.tsx", blockers=blocked_by("ELE-1", resolved=True)),
    )
    assert order.order == ["ELE-2"]
    assert order.done == ["ELE-1"]


def test_open_blockers_outside_the_epic_are_reported():
    order = order_of(
        story("ELE-1", "backend", "src/a.ts", blockers=blocked_by("OPS-7") + blocked_by("OPS-8", resolved=True)),
    )
    assert [b.key for b in order.external_blockers["ELE-1"]] == ["OPS-7"]


# -- the engine review ---------------------------------------------------------------------------
REVIEW = """Here is my review.
{"ready": false, "questions": ["ELE-2: which field name does the editor save?"],
 "contracts": [{"name": "UploadedMedia", "owner": "ELE-1", "used_by": ["ELE-2"], "definition": "{ publicUrl, key }"}],
 "conflicts": [{"stories": ["ELE-1", "ELE-2"], "detail": "positionX vs bannerPositionX", "resolution": "use bannerPositionX"}],
 "oversized": ["not a dict", {"story": "ELE-2", "reason": "two screens", "split": "one per screen"}]}"""


def epic_and_stories():
    epic = make_issue("ELE-100", "[Epic] Image upload", "ELE", "The whole thing", issue_type="Epic")
    stories = [
        story("ELE-1", "backend", "src/api/upload.ts"),
        story("ELE-2", "frontend", "src/ui/editor.tsx", blockers=blocked_by("ELE-1")),
    ]
    return epic, stories


def test_the_engine_review_is_parsed_and_unusable_entries_are_dropped():
    epic, stories = epic_and_stories()
    engine = FakeEngine(epic_plan=REVIEW)
    verdict = review_epic(engine, epic, stories, order_of(*stories), 1500, 60)

    assert verdict.ready is False
    assert verdict.questions == ["ELE-2: which field name does the editor save?"]
    assert verdict.contracts[0]["name"] == "UploadedMedia"
    assert verdict.conflicts[0]["resolution"] == "use bannerPositionX"
    assert verdict.oversized == [{"story": "ELE-2", "reason": "two screens", "split": "one per screen"}]
    [prompt] = engine.prompts
    assert prompt.startswith("EPIC PLAN for Jira epic ELE-100 and its 2 child stories.")


def test_questions_sent_as_objects_read_as_key_and_text():
    epic, stories = epic_and_stories()
    reply = '{"ready": false, "questions": [{"story": "ELE-1", "question": "Which route?"}, {"question": "Why?"}, {}]}'
    verdict = review_epic(FakeEngine(epic_plan=reply), epic, stories, order_of(*stories), 1500, 60)
    assert verdict.questions == ["ELE-1: Which route?", "Why?"]


def test_not_ready_without_questions_still_asks_something():
    epic, stories = epic_and_stories()
    verdict = review_epic(FakeEngine(epic_plan='{"ready": false}'), epic, stories, order_of(*stories), 1500, 60)
    assert verdict.ready is False and verdict.questions


@pytest.mark.parametrize("engine", [FakeEngine(epic_plan="I could not decide."), FakeEngine(fail=True)])
def test_an_unusable_or_failed_review_raises(engine):
    epic, stories = epic_and_stories()
    with pytest.raises(EpicPlanError):
        review_epic(engine, epic, stories, order_of(*stories), 1500, 60)


def test_the_prompt_lists_stories_in_stack_order_and_marks_done_ones():
    epic, stories = epic_and_stories()
    prompt = build_epic_plan_prompt(epic, list(reversed(stories)), [("ELE-1", "ELE-2", ["src/x.ts"])], {"ELE-1"}, 900)
    assert prompt.index('<ticket key="ELE-2">') < prompt.index('<ticket key="ELE-1">')
    assert "Story 2 (already done)" in prompt
    assert "- ELE-1 and ELE-2: src/x.ts" in prompt
    assert "about 900 changed lines" in prompt
    assert "blocked by ELE-1" in prompt


# -- the whole plan --------------------------------------------------------------------------------
class EpicJira:
    def __init__(self, epic, stories):
        self.epic, self.stories, self.calls = epic, stories, []

    def get_issue(self, key):
        self.calls.append(("get_issue", key))
        return self.epic

    def children(self, key):
        self.calls.append(("children", key))
        return self.stories


def test_plan_epic_reads_orders_and_reviews_without_writing(cfg):
    epic, stories = epic_and_stories()
    stories.append(make_issue("ELE-3", "FE: no detail", "ELE", "Make it nice", labels=["frontend"]))
    jira, engine = EpicJira(epic, stories), FakeEngine()

    report = plan_epic(jira, engine, "ELE-100", cfg)

    assert jira.calls == [("get_issue", "ELE-100"), ("children", "ELE-100")]  # reads only
    assert report.order.order == ["ELE-1", "ELE-2", "ELE-3"]
    assert report.verdict is not None and report.verdict.ready
    assert not report.blocking
    assert "ELE-3 has no Gherkin acceptance scenarios." in report.warnings
    assert "ELE-3 names no file paths under Target Files." in report.warnings
    text = format_report(report, "https://x.atlassian.net")
    assert "https://x.atlassian.net/browse/ELE-100" in text
    assert "  2. ELE-2 [FE] FE: ELE-2" in text and "after ELE-1 (blocks)" in text
    assert "Engine review: READY" in text


def test_a_cycle_blocks_the_epic_and_skips_the_engine(cfg):
    epic = make_issue("ELE-100", "[Epic] x", "ELE", "", issue_type="Epic")
    stories = [
        story("ELE-1", "backend", "src/a.ts", blockers=blocked_by("ELE-2")),
        story("ELE-2", "backend", "src/b.ts", blockers=blocked_by("ELE-1")),
    ]
    engine = FakeEngine()
    report = plan_epic(EpicJira(epic, stories), engine, "ELE-100", cfg)
    assert report.blocking and report.verdict is None and engine.prompts == []
    assert "cycle" in report.problems[0]


def test_an_epic_in_an_unconfigured_project_is_a_problem(cfg):
    epic = make_issue("OPS-1", "[Epic] x", "OPS", "", issue_type="Epic")
    report = plan_epic(EpicJira(epic, [story("ELE-1", "backend", "src/a.ts")]), None, "OPS-1", cfg)
    assert report.problems == ["No repo is configured for Jira project OPS."]
    assert "Engine review: skipped." in format_report(report)


def test_a_review_marked_not_ready_blocks_the_epic(cfg):
    epic, stories = epic_and_stories()
    report = plan_epic(EpicJira(epic, stories), FakeEngine(epic_plan=REVIEW), "ELE-100", cfg)
    assert report.blocking
    text = format_report(report)
    assert "Engine review: NOT READY" in text
    assert "- UploadedMedia (owner ELE-1; used by ELE-2): { publicUrl, key }" in text
    assert "- [ELE-1, ELE-2] positionX vs bannerPositionX Resolution: use bannerPositionX" in text


# -- Jira ------------------------------------------------------------------------------------------
def raw_issue(key: str) -> dict:
    return {"key": key, "fields": {"summary": key, "project": {"key": "ELE"},
                                   "status": {"name": "Done", "statusCategory": {"key": "done"}}}}


def test_children_follows_every_page(requests_mock):
    url = "https://x.atlassian.net/rest/api/3/search/jql"
    requests_mock.post(url, [
        {"json": {"issues": [raw_issue("ELE-1"), raw_issue("ELE-2")], "nextPageToken": "p2", "isLast": False}},
        {"json": {"issues": [raw_issue("ELE-3")], "isLast": True}},
    ])
    client = JiraClient("https://x.atlassian.net", "me@x.com", "tok")

    kids = client.children("ELE-100")

    assert [k.key for k in kids] == ["ELE-1", "ELE-2", "ELE-3"]
    assert kids[0].done and kids[0].status_category == "done"
    first, second = (r.json() for r in requests_mock.request_history)
    assert first["jql"] == 'parent = "ELE-100" ORDER BY key ASC' and "nextPageToken" not in first
    assert second["nextPageToken"] == "p2"


def test_children_refuses_a_key_that_is_not_one():
    client = JiraClient("https://x.atlassian.net", "me@x.com", "tok")
    with pytest.raises(ValueError):
        client.children('ELE-1" OR project = SECRET')


def test_running_an_epic_is_not_available_yet(capsys):
    assert main(["epic", "ELE-100"]) == 2
    assert "--plan" in capsys.readouterr().err
