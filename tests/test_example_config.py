"""config.example.toml is documentation people copy, so it must keep loading and keep saying what it claims."""

from __future__ import annotations

from pathlib import Path

from dev_bot.config import load_config


def test_the_example_config_stays_loadable():
    cfg = load_config(Path(__file__).parent.parent / "config.example.toml", env_file=Path("none.env"))
    assert [r.name for r in cfg.repos] == [
        "roselletabuena/hirangtally-fullstack",
        "roselletabuena/figma-to-react-design-system",
    ]
    design = cfg.repos[1]
    assert design.workflows == ["bugfix", "task"] and not design.spec_aware and design.instructions.strip()
    assert cfg.search_jql().startswith('project in ("VS", "AD") AND')
    # AD's board says "For Review" where VS says "In Review"; each project moves its tickets to its own status.
    assert cfg.transition("AD", "in_review") == "For Review" and cfg.transition("VS", "in_review") == "In Review"
    assert cfg.transition("AD", "in_progress") == "In Progress" and cfg.transition("AD", "needs_info") == "To Do"
