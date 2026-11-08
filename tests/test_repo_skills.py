"""The repo's own skills: how they are found and filtered, and what the engine is told about them."""

from __future__ import annotations

import logging

import pytest
from conftest import make_issue, write_skill

from dev_bot.config import RepoConfig
from dev_bot.jira import Issue
from dev_bot.prompt import (
    build_bugfix_prompt,
    build_repair_prompt,
    build_stage_prompt,
    build_task_prompt,
)
from dev_bot.skills import (
    Guidance,
    discover_skills,
    gather_guidance,
    parse_frontmatter,
    select_skills,
)


@pytest.fixture
def issue() -> Issue:
    return make_issue(key="DS-1", summary="Add badge", project="DS", description="Make a badge")


# -- reading skills ------------------------------------------------------------------------------
def test_frontmatter_reads_plain_quoted_and_folded_values():
    text = (
        "---\n"
        "name: plain\n"
        "description: 'Review the repo''s changes: carefully'\n"
        "metadata:\n"
        '  version: "1.0"\n'
        "---\nbody\n"
    )
    assert parse_frontmatter(text) == {
        "name": "plain",
        "description": "Review the repo's changes: carefully",
        "metadata": 'version: "1.0"',
    }
    folded = "---\nname: f\ndescription: >-\n  Audits colors across\n  the interface.\n---\n"
    assert parse_frontmatter(folded)["description"] == "Audits colors across the interface."
    assert parse_frontmatter('---\nname: d\ndescription: "Say \\"hi\\""\n---\n')["description"] == 'Say "hi"'
    assert parse_frontmatter("# no frontmatter\n") == {}


def test_discovery_prefers_agents_over_claude_and_sorts(tmp_path):
    write_skill(tmp_path, ".claude/skills", "shared", "name: shared\ndescription: the CLAUDE copy")
    write_skill(tmp_path, ".agents/skills", "shared", "name: shared\ndescription: the AGENTS copy")
    write_skill(tmp_path, ".claude/skills", "only-claude")
    write_skill(tmp_path, ".agents/skills", "alpha")
    skills = discover_skills(tmp_path, RepoConfig(name="a/b"))
    assert [s.name for s in skills] == ["alpha", "only-claude", "shared"]
    shared = next(s for s in skills if s.name == "shared")
    assert shared.description == "the AGENTS copy" and shared.path == ".agents/skills/shared/SKILL.md"


def test_a_skill_without_usable_frontmatter_is_still_listed_by_folder_name(tmp_path):
    folder = tmp_path / ".agents" / "skills" / "bare"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Just a heading, no header block\n", encoding="utf-8")
    (tmp_path / ".agents" / "skills" / "stray-file.md").write_text("not a skill folder", encoding="utf-8")
    skills = discover_skills(tmp_path, RepoConfig(name="a/b"))
    assert [(s.name, s.description) for s in skills] == [("bare", "")]


def test_a_repo_without_skills_yields_nothing(tmp_path):
    assert discover_skills(tmp_path, RepoConfig(name="a/b")) == []


def test_long_descriptions_are_shortened_on_a_word_boundary(tmp_path):
    write_skill(tmp_path, ".agents/skills", "wordy", "name: wordy\ndescription: " + "word " * 100)
    description = discover_skills(tmp_path, RepoConfig(name="a/b"))[0].description
    assert len(description) <= 205 and description.endswith("word...")


def test_select_applies_include_then_exclude_then_cap(tmp_path, caplog):
    for name in ("react-core", "react-perf", "git-commit", "design"):
        write_skill(tmp_path, ".agents/skills", name)
    found = discover_skills(tmp_path, RepoConfig(name="a/b"))
    names = lambda repo: [s.name for s in select_skills(found, repo)]
    assert names(RepoConfig(name="a/b")) == ["design", "git-commit", "react-core", "react-perf"]
    assert names(RepoConfig(name="a/b", skills_exclude=["git-*"])) == ["design", "react-core", "react-perf"]
    assert names(RepoConfig(name="a/b", skills_include=["react-*", "design"], skills_exclude=["*-perf"])) == [
        "design",
        "react-core",
    ]
    with caplog.at_level(logging.WARNING, logger="dev_bot"):
        assert names(RepoConfig(name="a/b", skills_max=2)) == ["design", "git-commit"]
    assert "skills_max" in caplog.text


def test_guidance_resolves_the_bugfix_skill_even_when_it_is_not_offered(tmp_path):
    write_skill(tmp_path, ".agents/skills", "spec-aware-bugfix")
    write_skill(tmp_path, ".agents/skills", "tdd")
    repo = RepoConfig(name="a/b", skills_include=["tdd"], bugfix_skill="spec-aware-bugfix")
    guide = gather_guidance(tmp_path, repo)
    assert [s.name for s in guide.skills] == ["tdd"]
    assert guide.bugfix_skill.path == ".agents/skills/spec-aware-bugfix/SKILL.md"
    assert gather_guidance(tmp_path, RepoConfig(name="a/b", bugfix_skill="missing")).bugfix_skill is None


def test_guidance_drops_read_first_files_that_do_not_exist(tmp_path, caplog):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "tokens.md").write_text("x", encoding="utf-8")
    repo = RepoConfig(name="a/b", read_first=["rules/tokens.md", "rules/gone.md"])
    with caplog.at_level(logging.WARNING, logger="dev_bot"):
        assert gather_guidance(tmp_path, repo).read_first == ["rules/tokens.md"]
    assert "gone.md" in caplog.text


def test_skill_file_falls_back_to_the_conventional_location(tmp_path):
    write_skill(tmp_path, ".claude/skills", "speckit-plan")
    guide = gather_guidance(tmp_path, RepoConfig(name="a/b"))
    assert guide.skill_file("speckit-plan") == ".claude/skills/speckit-plan/SKILL.md"
    assert guide.skill_file("speckit-tasks") == ".agents/skills/speckit-tasks/SKILL.md"


# -- what the engine is told -----------------------------------------------------------------------
@pytest.fixture
def guide(tmp_path) -> Guidance:
    write_skill(tmp_path, ".agents/skills", "create-component", "name: create-component\ndescription: Scaffold a component.")
    write_skill(tmp_path, ".agents/skills", "git-atomic-commit")
    (tmp_path / "rules.md").write_text("x", encoding="utf-8")
    repo = RepoConfig(name="a/b", skills_exclude=["git-*"], read_first=["rules.md"])
    return gather_guidance(tmp_path, repo)


def test_task_prompt_points_the_engine_at_the_repos_skills(issue, guide):
    prompt = build_task_prompt(issue, RepoConfig(name="a/b"), "", guide)
    assert "- create-component: Scaffold a component. (.agents/skills/create-component/SKILL.md)" in prompt
    assert "read the SKILL.md of every skill below that applies" in prompt
    assert "git-atomic-commit" not in prompt  # excluded
    assert "Read these files first" in prompt and "- rules.md" in prompt
    assert "skip that step: the bot does it" in prompt  # commit / PR / run steps in a skill are the bot's job
    assert f"Today's date is {guide.today}." in prompt


def test_without_skills_the_prompts_carry_no_skills_section(issue):
    repo = RepoConfig(name="a/b")
    for prompt in (build_task_prompt(issue, repo), build_bugfix_prompt(issue, repo)):
        assert "Repository skills" not in prompt and "Read these files first" not in prompt


def test_repo_instructions_reach_every_code_writing_prompt(issue, guide):
    repo = RepoConfig(name="a/b", instructions="Never add visual specs.")
    prompts = [
        build_task_prompt(issue, repo, "", guide),
        build_bugfix_prompt(issue, repo, "", "", guide),
        build_stage_prompt("feature.implement", issue, repo, "001-x", "https://j", "", guide),
        build_repair_prompt(issue, repo, "npm run lint", "boom"),
    ]
    assert all("Rules specific to this repository:\nNever add visual specs." in p for p in prompts)


def test_bugfix_prompt_names_the_repos_own_bugfix_skill(tmp_path, issue):
    write_skill(tmp_path, ".agents/skills", "spec-aware-bugfix")
    repo = RepoConfig(name="a/b", bugfix_skill="spec-aware-bugfix")
    prompt = build_bugfix_prompt(issue, repo, "", "specs/001-x", gather_guidance(tmp_path, repo))
    assert "following the repo's bugfix method (.agents/skills/spec-aware-bugfix/SKILL.md; read it first)" in prompt
    assert "B = stale spec" in prompt and "ESCALATE: amend" in prompt and "specs/001-x" in prompt


def test_stage_prompts_name_the_skill_wherever_the_repo_keeps_it(tmp_path, issue):
    write_skill(tmp_path, ".claude/skills", "speckit-plan")
    repo = RepoConfig(name="a/b")
    prompt = build_stage_prompt("feature.plan", issue, repo, "001-x", "https://j", "", gather_guidance(tmp_path, repo))
    assert "`.claude/skills/speckit-plan/SKILL.md`" in prompt
