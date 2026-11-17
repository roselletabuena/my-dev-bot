"""Epic mode, the part that writes: build an Epic's stories one after another as a stack of draft PRs.

The order comes from `epic.plan_epic`. Each story is built on the branch of the last story that was built,
so its PR shows only its own change and the stories merge from the bottom of the stack up.

A story that fails, is handed back with questions, or is blocked does not stop the Epic. The stories that
depend on it (an "is blocked by" link, or both name the same files) are skipped, and the stories that do
not depend on it are built on the last branch that did get built. Running the Epic again picks up where it
stopped: a story that already has an open PR is left alone, and a story that did not get built is retried.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .epic import EpicReport, EpicVerdict, plan_epic
from .pipeline import Pipeline, StackSlot
from .prompt import build_epic_context_prompt

log = logging.getLogger("dev_bot")

BUILT = ("pr_opened", "dry_run_ok", "existing")  # statuses that leave the story's work available to later ones


@dataclass
class StoryResult:
    key: str
    status: str  # pr_opened | dry_run_ok | existing | needs_info | failed | skipped | deferred
    detail: str = ""
    pr_url: str | None = None


@dataclass
class EpicRun:
    report: EpicReport | None  # None when the bot was paused before the Epic was even read
    results: list[StoryResult] = field(default_factory=list)
    dry_run: bool = False
    halted: str = ""  # why nothing was attempted, when that is not a problem with the Epic

    @property
    def failed(self) -> bool:
        return any(r.status == "failed" for r in self.results)


def _mentions(key: str, *values: object) -> bool:
    return any(key == str(v) for v in values)


def _context(
    report: EpicReport, key: str, position: int, in_stack: list[str], verdict: EpicVerdict | None
) -> str:
    by_key = {s.key: s for s in report.stories}
    order = report.order.order
    contracts = [
        c for c in (verdict.contracts if verdict else [])
        if _mentions(key, c.get("owner"), *(c.get("used_by") or []))
    ]
    conflicts = [c for c in (verdict.conflicts if verdict else []) if _mentions(key, *(c.get("stories") or []))]
    return build_epic_context_prompt(
        report.epic, by_key[key], position, len(order),
        earlier=[by_key[k] for k in in_stack], later=[by_key[k] for k in order[position:]],
        contracts=contracts, conflicts=conflicts,
    )


def _pr_note(report: EpicReport, jira_base_url: str, position: int, base: str, base_key: str) -> str:
    epic = report.epic
    where = f"[{epic.key}]({jira_base_url.rstrip('/')}/browse/{epic.key})"
    based = f"`{base}` ({base_key}), so merge the stories from the bottom of the stack up" if base_key else f"`{base}`"
    return f"_Part of a stack: story {position} of {len(report.order.order)} in Epic {where}. This PR is based on {based}._"


def run_epic(pipeline: Pipeline, key: str, use_engine: bool = True, replan: bool = False) -> EpicRun:
    """Plan the Epic, then build each of its stories in order. Returns what happened to every story.

    Raises nothing for a story that goes wrong: that story's result says so and the Epic carries on.
    """
    cfg, state, repos, github = pipeline.cfg, pipeline.state, pipeline.repos, pipeline.github
    if cfg.paused_file.exists():
        pipeline.notifier.send(f"dev-bot paused ({cfg.paused_file} exists); nothing done.")
        return EpicRun(None, dry_run=pipeline.dry_run, halted="paused")

    # The review is saved the first time it says "ready" and reused after that: stories built on
    # different days must agree on the same names, and a fresh review would word them differently.
    saved = None if replan or not use_engine else state.load_epic_plan(key)
    verdict = EpicVerdict.from_dict(saved) if saved else None
    report = plan_epic(pipeline.jira, pipeline.engine if use_engine else None, key, cfg, verdict)
    run = EpicRun(report, dry_run=pipeline.dry_run)
    if report.blocking:
        return run
    if report.verdict is not None and verdict is None and not pipeline.dry_run:
        state.save_epic_plan(key, report.verdict.to_dict())
    verdict = report.verdict

    repo = cfg.repo_for(report.epic.project)
    assert repo is not None  # plan_epic reports a missing repo as a problem
    repos.prune_stale()
    repos.ensure_cache(repo)  # fresh view of origin, to see the branches earlier runs pushed

    order = report.order
    by_key = {s.key: s for s in report.stories}
    done = set(order.done)
    deps = {k: [e.before for e in order.predecessors(k) if e.before not in done] for k in order.order}

    tip, tip_key = repo.default_branch, ""  # the branch the next story is built on, and the story that made it
    in_stack: list[str] = []  # stories whose work is in `tip`, oldest first
    contained: set[str] = set()  # stories whose work later stories may rely on (also dry-run successes)
    unbuilt: dict[str, str] = {}  # story -> status it ended in; its dependents are skipped
    budget = cfg.daily_cap - state.count_today()

    def finish(story_key: str, status: str, detail: str = "", pr_url: str | None = None) -> None:
        run.results.append(StoryResult(story_key, status, detail, pr_url))
        if status not in BUILT:
            unbuilt[story_key] = status
        log.info("epic %s: %s %s%s", key, story_key, status, f" - {_first_line(detail)}" if detail else "")

    for position, story_key in enumerate(order.order, 1):
        story = by_key[story_key]
        branch = pipeline.branch_for(story)

        existing = github.find_open_pr(repo.name, branch)
        if existing:
            finish(story_key, "existing", "already has an open PR", existing.url)
            if tip == repo.default_branch or repos.is_ancestor(repo, tip, branch):
                tip, tip_key = branch, story_key
                in_stack.append(story_key)
                contained.add(story_key)
            continue

        halt = "the bot is paused" if cfg.paused_file.exists() else "the daily ticket cap is reached" if budget <= 0 else ""
        if halt:  # everything left waits for the next run, whether or not it would have been skipped
            finish(story_key, "deferred", f"{halt}; run the Epic again to continue")
            continue

        blocked_by = [d for d in deps[story_key] if d in unbuilt]
        if blocked_by:
            finish(story_key, "skipped", "depends on " + ", ".join(f"{d} ({unbuilt[d]})" for d in blocked_by))
            continue
        apart = [d for d in deps[story_key] if d not in contained]
        if apart:
            finish(
                story_key, "skipped",
                f"{', '.join(apart)} has a PR that is not under {tip}; rebase the stack, then run the Epic again",
            )
            continue

        slot = StackSlot(
            base_branch=tip,
            satisfied=frozenset(deps[story_key]),
            context=_context(report, story_key, position, in_stack, verdict),
            pr_note=_pr_note(report, cfg.jira.base_url, position, tip, tip_key),
        )
        outcome = pipeline.process(story, slot)
        budget -= 1
        finish(story_key, outcome.status, outcome.detail, outcome.pr_url)
        if outcome.status == "pr_opened":
            tip, tip_key = branch, story_key
            in_stack.append(story_key)
        if outcome.status in BUILT:
            contained.add(story_key)

    pipeline.notifier.send(format_run(run))
    return run


def _first_line(text: str, limit: int = 160) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def format_run(run: EpicRun) -> str:
    assert run.report is not None
    epic = run.report.epic
    results = run.results
    built = sum(1 for r in results if r.status in BUILT)
    lines = [f"dev-bot epic {epic.key}: {built} of {len(results)} stories built" + (" (dry run)" if run.dry_run else "")]
    for n, r in enumerate(results, 1):
        tail = r.pr_url or _first_line(r.detail)
        lines.append(f"{n:>3}. {r.key}: {r.status}" + (f" {tail}" if tail else ""))
    if run.dry_run and any(r.status == "dry_run_ok" for r in results):
        lines.append("Dry run: nothing was pushed, so each story was built on the branch of the last real PR, not on the one before it.")
    return "\n".join(lines)
