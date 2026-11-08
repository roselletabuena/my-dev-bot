from __future__ import annotations

from conftest import FILLED, TEMPLATE, FakeEngine, make_issue
from dev_bot.guardrails import CheckResult
from dev_bot.jira import Issue
from dev_bot.pr_description import (
    _strip_fence,
    build_description,
    finalize,
    is_valid_fill,
    terminal_summary,
    tick_passed_checks,
)

PASS = CheckResult('python -c "print(1)"', True, "line one\nTests  12 passed (12)")
FAIL = CheckResult("npm run other", False, "boom")


# -- through the whole pipeline ----------------------------------------------------------------
def test_pr_body_is_the_filled_template_with_verified_testing_section(build):
    pipeline, _, gh, _ = build(FakeEngine())
    assert pipeline.process(make_issue()).status == "pr_opened"
    body = gh.opened[0]["body"]

    assert "Changes the greeting for ELE-1" in body  # engine-written narrative is in
    assert "VS-XX" not in body and "<!-- Concise summary" not in body  # placeholders replaced
    assert "- [x] **Check**: `python -c \"print(1)\"`" in body  # ticked by the bot: it ran and passed
    assert "- [ ] **Not run by the bot**" in body  # never ticked without evidence
    assert "- [ ] **§I**" in body  # constitution left for the human reviewer
    assert "$ python -c \"print(1)\"  ->  PASS" in body and "<!-- Paste" not in body
    assert body.rstrip().endswith("Please review before merging._")


def test_description_prompt_carries_template_and_real_diff(build):
    engine = FakeEngine()
    pipeline, _, _, _ = build(engine)
    pipeline.process(make_issue())
    [prompt] = [p for p in engine.prompts if p.startswith("PR DESCRIPTION")]

    assert "## 🎯 Executive Summary" in prompt  # the template
    assert "+hello electa" in prompt  # the actual diff the engine produced
    assert "ELE-1" in prompt


def test_failed_description_call_falls_back_and_still_opens_the_pr(build):
    pipeline, _, gh, _ = build(FakeEngine(description="fail"))
    assert pipeline.process(make_issue()).status == "pr_opened"
    body = gh.opened[0]["body"]

    assert "Checks run by the bot" in body and "ELE-1" in body
    assert "Executive Summary" not in body  # no unfilled template dumped into the PR


def test_unparseable_description_falls_back(build):
    pipeline, _, gh, _ = build(FakeEngine(description="garbage"))
    assert pipeline.process(make_issue()).status == "pr_opened"
    assert "Sure, here is a PR" not in gh.opened[0]["body"]
    assert "Checks run by the bot" in gh.opened[0]["body"]


# -- units -------------------------------------------------------------------------------------
def test_tick_only_ticks_passed_commands():
    out = tick_passed_checks(TEMPLATE, [PASS, FAIL])
    assert "- [x] **Check**" in out
    assert "- [ ] **Not run by the bot**" in out and "- [ ] **§I**" in out
    assert tick_passed_checks(TEMPLATE, [CheckResult('python -c "print(1)"', False, "")]).count("[x]") == 0


def test_terminal_summary_uses_last_lines_and_strips_ansi():
    r = CheckResult("npm run lint", True, "\x1b[32mold\x1b[0m\na\nb\nc\nd\ne")
    text = terminal_summary([r], tail_lines=2)
    assert text == "$ npm run lint  ->  PASS\nd\ne"


def test_valid_fill_requires_all_template_headings_in_order():
    assert is_valid_fill(TEMPLATE, FILLED)
    assert not is_valid_fill(TEMPLATE, "just prose")
    assert not is_valid_fill(TEMPLATE, FILLED.replace("## 🎯 Executive Summary", "## Summary"))
    assert not is_valid_fill("no headings at all", "anything")


def test_strip_fence_unwraps_a_markdown_fence():
    assert _strip_fence("```markdown\n## A\ntext\n```") == "## A\ntext"
    assert _strip_fence("## A\ntext") == "## A\ntext"


def test_finalize_adds_jira_link_when_the_model_dropped_it_and_notes_repairs():
    issue = Issue(key="ELE-7", summary="Do it", description="", project="ELE")
    out = finalize("## Body", issue, "https://j/browse/ELE-7", [PASS], repairs=2)
    assert out.startswith("Jira: [ELE-7](https://j/browse/ELE-7)")
    assert "Checks passed after 2 automatic fix round(s)." in out
    assert "### Bot check output" in out  # template had no output block, so the bot appends one


def test_no_template_means_fallback_without_calling_the_engine():
    engine = FakeEngine()
    body = build_description(engine, make_issue(), "https://j", "", "diff", [PASS], 0, 10)
    assert "Checks run by the bot" in body and engine.prompts == []
