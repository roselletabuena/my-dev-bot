from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import guardrails, triage
from .config import Config, RepoConfig
from .engine.base import CodeEngine
from .github import GitHubClient
from .jira import Issue, JiraClient
from .notify import Notifier
from .orchestrator import RoutingError, route_ticket
from .pr_description import build_description
from .prompt import build_repair_prompt
from .repo import RepoManager, slugify
from .state import State
from .workflows import Plan, build_plan

log = logging.getLogger("dev_bot")


@dataclass
class Outcome:
    key: str
    status: str  # pr_opened | dry_run_ok | needs_info | failed | skipped
    detail: str = ""
    pr_url: str | None = None
    workflow: str = ""  # bugfix | feature | amend | task, once the ticket has been routed


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        jira: JiraClient,
        repos: RepoManager,
        github: GitHubClient,
        engine: CodeEngine,
        state: State,
        notifier: Notifier,
        dry_run: bool = False,
    ):
        self.cfg = cfg
        self.jira = jira
        self.repos = repos
        self.github = github
        self.engine = engine
        self.state = state
        self.notifier = notifier
        self.dry_run = dry_run

    # -- batch ---------------------------------------------------------------------------------
    def run_once(self) -> list[Outcome]:
        if self.cfg.paused_file.exists():
            self.notifier.send(f"dev-bot paused ({self.cfg.paused_file} exists); nothing done.")
            return []
        budget = min(self.cfg.max_tickets_per_run, self.cfg.daily_cap - self.state.count_today())
        if budget <= 0:
            self.notifier.send("dev-bot: daily ticket cap reached; nothing done.")
            return []

        self.repos.prune_stale()
        issues = self.jira.search(self.cfg.search_jql(), max_results=budget)
        log.info("poll: %d ticket(s) matched (budget %d)", len(issues), budget)
        outcomes = [self.process(issue) for issue in issues[:budget]]
        if outcomes:
            self.notifier.send(self.summary(outcomes))
        return outcomes

    @staticmethod
    def summary(outcomes: list[Outcome]) -> str:
        lines = [f"dev-bot run: {len(outcomes)} ticket(s)"]
        for o in outcomes:
            tail = f" {o.pr_url}" if o.pr_url else f" ({o.detail})" if o.detail else ""
            lines.append(f"- {o.key}: {o.status}" + (f" [{o.workflow}]" if o.workflow else "") + tail)
        return "\n".join(lines)

    # -- one ticket ----------------------------------------------------------------------------
    def process(self, issue: Issue) -> Outcome:
        repo = self.cfg.repo_for(issue.project)
        if repo is None:
            return Outcome(issue.key, "skipped", f"no repo configured for Jira project {issue.project}")
        try:
            return self._process(issue, repo)
        except Exception as exc:  # one bad ticket must never stop the batch
            log.exception("unexpected failure on %s", issue.key)
            return self._fail(issue, f"Unexpected error: {exc}")

    def _process(self, issue: Issue, repo: RepoConfig) -> Outcome:
        if issue.open_blockers:
            return self._blocked(issue)

        self._claim(issue)

        readiness = triage.check_readiness(self.engine, issue, self.cfg.engine_timeout_s)
        if not readiness.ready:
            return self._bounce(issue, "The bot needs more detail before it can implement this", readiness.questions)

        branch = f"bot/{issue.key}-{slugify(issue.summary)}"
        worktree = self.repos.create_worktree(repo, branch)
        try:
            base = self.repos.head_sha(worktree)
            agents_md = self._read(worktree / "AGENTS.md")
            try:
                route = route_ticket(issue, worktree, repo, self.cfg, self.engine)
            except RoutingError as exc:
                return self._bounce(issue, "The bot could not tell which workflow this ticket needs", exc.questions)
            log.info("%s: workflow %s (%s)", issue.key, route.workflow, route.reason)
            self._say(issue, f"Workflow: {route.workflow} ({route.reason}).")
            plan = build_plan(route, issue, repo, worktree, self.cfg, agents_md)
            return self._implement(issue, repo, plan, branch, worktree, base)
        finally:
            self.repos.remove_worktree(repo, worktree)

    def _implement(self, issue: Issue, repo: RepoConfig, plan: Plan, branch: str, worktree: Path, base: str) -> Outcome:
        # Setup runs BEFORE the engine so the repo's own tooling (node_modules, lint hooks, a skills
        # sync) is in place while the agent edits. Setup output is re-checked by the final diff guard.
        command_env = repo.command_env(self.cfg.work_dir)
        failed = self._first_failure(guardrails.run_commands(repo.setup_commands, worktree, extra_env=command_env))
        if failed:
            return self._fail(issue, f"Setup `{failed.command}` failed:\n{failed.output_tail}", plan.workflow)

        rules = plan.repo  # spec workflows may also write the spec folder, with a larger diff cap
        for stage in plan.stages:
            during = f" during the {stage.name} stage" if len(plan.stages) > 1 else ""
            result = self.engine.run(worktree, stage.prompt, self.cfg.engine_timeout_s)
            if not result.ok:
                return self._fail(issue, f"The coding engine failed{during}: {result.error}", plan.workflow)
            handback = stage.halts(result.response)
            if handback:  # e.g. a bugfix the agent judged to need a product decision: no code, a human answers
                return self._bounce(issue, "The bot stopped without changing code", [handback], plan.workflow)
            changes = self.repos.collect_changes(worktree, base)
            problems = guardrails.check_changes(changes, rules)
            if problems:
                bullets = "\n".join(f"- {p}" for p in problems)
                return self._fail(issue, f"Guardrails rejected the change{during}:\n{bullets}", plan.workflow)
            problem = stage.verify(worktree, changes.files, result.response)
            if problem:
                log.warning("%s: %s stage incomplete; engine reply:\n%s", issue.key, stage.name, result.response.strip())
                return self._fail(issue, f"The {stage.name} stage is incomplete: {problem}", plan.workflow)

        # Checks may fail on code the engine just wrote (lint, types, tests). Feed the failure back
        # for a bounded number of repair rounds; each round re-verifies the diff before re-checking.
        repairs = 0
        while True:
            check_results = guardrails.run_commands(repo.check_commands, worktree, extra_env=command_env)
            failed = self._first_failure(check_results)
            if failed is None:
                break
            if repairs >= self.cfg.repair_rounds:
                suffix = f" (after {repairs} repair attempt(s))" if repairs else ""
                return self._fail(issue, f"`{failed.command}` failed{suffix}:\n{failed.output_tail}", plan.workflow)
            repairs += 1
            log.info("%s: `%s` failed; repair round %d/%d", issue.key, failed.command, repairs, self.cfg.repair_rounds)
            fix = self.engine.run(
                worktree, build_repair_prompt(issue, rules, failed.command, failed.output_tail), self.cfg.engine_timeout_s
            )
            if not fix.ok:
                return self._fail(issue, f"The coding engine failed during repair: {fix.error}", plan.workflow)
            problems = guardrails.check_changes(self.repos.collect_changes(worktree, base), rules)
            if problems:
                bullets = "\n".join(f"- {p}" for p in problems)
                return self._fail(issue, f"Guardrails rejected the repaired change:\n{bullets}", plan.workflow)

        # Setup/checks can modify files (lockfiles, formatters), so re-verify the final diff.
        changes = self.repos.collect_changes(worktree, base)
        problems = guardrails.check_changes(changes, rules)
        if problems:
            bullets = "\n".join(f"- {p}" for p in problems)
            return self._fail(issue, f"Guardrails rejected the final change:\n{bullets}", plan.workflow)

        if self.dry_run:
            detail = f"{len(changes.files)} file(s), {changes.lines} line(s)"
            return Outcome(issue.key, "dry_run_ok", detail, workflow=plan.workflow)

        self.repos.commit(worktree, f"{issue.key}: {issue.summary}", base)
        self.repos.push(worktree, branch)
        template = self._read(worktree / ".github" / "pull_request_template.md")
        body = build_description(
            self.engine,
            issue,
            self.cfg.jira.base_url,
            template,
            self.repos.diff_text(worktree, base),
            check_results,
            repairs,
            self.cfg.engine_timeout_s,
        )
        pr = self.github.open_draft_pr(
            repo.name, branch, repo.default_branch, f"{issue.key}: {issue.summary}", body, labels=["bot"]
        )
        self._say(issue, f"Draft PR opened: {pr.url}")
        self._move(issue, "in_review")
        return self._finish(Outcome(issue.key, "pr_opened", pr_url=pr.url, workflow=plan.workflow))

    # -- helpers -------------------------------------------------------------------------------
    @staticmethod
    def _first_failure(results: list[guardrails.CheckResult]) -> guardrails.CheckResult | None:
        return next((r for r in results if not r.ok), None)

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def _write(self, fn, *args) -> None:
        """Run a Jira write unless this is a dry run."""
        if self.dry_run:
            log.info("[dry-run] skip %s%s", fn.__name__, args)
            return
        fn(*args)

    def _claim(self, issue: Issue) -> None:
        self._write(self.jira.add_label, issue.key, self.cfg.jira.claimed_label)
        self._move(issue, "in_progress")
        self._say(issue, "Bot picked this up.")

    def _say(self, issue: Issue, text: str) -> None:
        self._write(self.jira.comment, issue.key, text)

    def _move(self, issue: Issue, which: str) -> None:
        name = self.cfg.transition(issue.project, which)
        if self.dry_run:
            log.info("[dry-run] skip transition %s -> %s", issue.key, name)
        elif not self.jira.transition(issue.key, name):
            log.warning("%s: no transition named %r available", issue.key, name)

    def _blocked(self, issue: Issue) -> Outcome:
        """Still blocked by an unresolved ticket: leave the claim/status alone so the next poll re-checks it."""
        names = ", ".join(f"{b.key} ({b.status})" for b in issue.open_blockers)
        detail = f"Blocked by {names}"
        self._say(issue, f"{detail}. The bot will pick this up once it's unblocked.")
        self.notifier.send(f"dev-bot: {issue.key} is blocked by {names}; skipping.")
        return self._finish(Outcome(issue.key, "skipped", detail))

    def _bounce(self, issue: Issue, intro: str, questions: list[str], workflow: str = "") -> Outcome:
        """Hand the ticket back with questions and release the claim so it can be picked up again."""
        text = "\n".join(f"- {q}" for q in questions)
        self._write(self.jira.remove_label, issue.key, self.cfg.jira.claimed_label)
        self._say(issue, f"{intro}:\n{text}")
        self._move(issue, "needs_info")
        return self._finish(Outcome(issue.key, "needs_info", text, workflow=workflow))

    def _fail(self, issue: Issue, reason: str, workflow: str = "") -> Outcome:
        # The claimed label stays so the ticket is not re-picked; a human decides what happens next.
        try:
            self._say(issue, f"The bot could not complete this ticket.\n{reason}")
            self._move(issue, "needs_info")
        except Exception:  # reporting must not mask the original failure
            log.exception("could not report failure on %s", issue.key)
        self.notifier.send(f"dev-bot: {issue.key} failed: {reason[:300]}")
        return self._finish(Outcome(issue.key, "failed", reason, workflow=workflow))

    def _finish(self, outcome: Outcome) -> Outcome:
        if not self.dry_run:
            self.state.record(outcome.key, outcome.status, outcome.detail, outcome.pr_url)
        return outcome
