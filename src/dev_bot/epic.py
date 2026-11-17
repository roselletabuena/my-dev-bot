"""Epic mode, read-only part: fetch an Epic's stories, order them into one stack, and review the Epic.

The stories are implemented one after another, each on a branch stacked on the previous story's branch,
so the order must respect every "is blocked by" link between them. Stories that name the same files are
also kept in a fixed order, so the earlier one's change is the base the later one builds on.

Nothing here writes to Jira or GitHub.
"""

from __future__ import annotations

import heapq
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .engine.base import CodeEngine
from .jira import Blocker, Issue, JiraClient
from .prompt import build_epic_plan_prompt
from .story import StoryContract, parse_story, shared_paths
from .triage import extract_json

log = logging.getLogger("dev_bot")

_SIDE_RANK = {"backend": 0, "": 1, "frontend": 2}  # tie-break: backend first, then unlabelled, then frontend
DEFAULT_QUESTION = "Please add the missing detail so every story can be implemented without guessing."


class EpicPlanError(RuntimeError):
    """The engine's review of the Epic failed or could not be read."""


@dataclass
class Edge:
    before: str
    after: str
    kind: str  # "blocks": a Jira link | "overlap": both stories name the same files
    detail: str = ""


@dataclass
class EpicOrder:
    order: list[str]  # stories to implement, first to last; Done stories are left out
    edges: list[Edge] = field(default_factory=list)
    overlaps: list[tuple[str, str, list[str]]] = field(default_factory=list)
    cycle: list[str] = field(default_factory=list)  # non-empty when the links cannot be ordered
    external_blockers: dict[str, list[Blocker]] = field(default_factory=dict)  # open blockers outside the Epic
    done: list[str] = field(default_factory=list)

    def predecessors(self, key: str) -> list[Edge]:
        return [e for e in self.edges if e.after == key]


@dataclass
class EpicVerdict:
    ready: bool
    questions: list[str] = field(default_factory=list)
    contracts: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    oversized: list[dict] = field(default_factory=list)


@dataclass
class EpicReport:
    epic: Issue
    stories: list[Issue]
    contracts: dict[str, StoryContract]
    order: EpicOrder
    verdict: EpicVerdict | None = None  # None when the engine review was skipped
    problems: list[str] = field(default_factory=list)  # anything that stops the Epic from starting
    warnings: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return bool(self.problems) or (self.verdict is not None and not self.verdict.ready)


# -- ordering ----------------------------------------------------------------------------------
def _rank(issue: Issue, contract: StoryContract) -> tuple:
    project, _, number = issue.key.rpartition("-")
    return (_SIDE_RANK.get(contract.side, 1), project, int(number) if number.isdigit() else 0, issue.key)


def _reaches(succ: dict[str, set[str]], start: str, goal: str) -> bool:
    seen, stack = set(), [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(succ[node])
    return False


def _find_cycle(succ: dict[str, set[str]]) -> list[str]:
    """One cycle as [a, b, ..., a], or [] when the graph is acyclic."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in succ}
    path: list[str] = []

    def visit(node: str) -> list[str]:
        color[node] = GREY
        path.append(node)
        for nxt in sorted(succ[node]):
            if color[nxt] == GREY:
                return path[path.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = visit(nxt)
                if found:
                    return found
        path.pop()
        color[node] = BLACK
        return []

    for node in sorted(succ):
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return []


def order_stories(stories: list[Issue], contracts: dict[str, StoryContract]) -> EpicOrder:
    by_key = {s.key: s for s in stories}
    rank = {s.key: _rank(s, contracts[s.key]) for s in stories}
    succ: dict[str, set[str]] = {k: set() for k in by_key}
    edges: list[Edge] = []
    external: dict[str, list[Blocker]] = {}

    for story in stories:
        for blocker in story.blockers:
            if blocker.key in by_key:
                if story.key not in succ[blocker.key]:
                    succ[blocker.key].add(story.key)
                    edges.append(Edge(blocker.key, story.key, "blocks"))
            elif not blocker.resolved:
                external.setdefault(story.key, []).append(blocker)

    done = [k for k in sorted(by_key, key=rank.__getitem__) if by_key[k].done]
    cycle = _find_cycle(succ)
    if cycle:
        return EpicOrder([], edges, [], cycle, external, done)

    # Stories naming the same files: keep them in rank order unless the links already order them.
    keys = sorted(by_key, key=rank.__getitem__)
    overlaps: list[tuple[str, str, list[str]]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = shared_paths(contracts[a], contracts[b])
            if not shared:
                continue
            overlaps.append((a, b, shared))
            if _reaches(succ, a, b) or _reaches(succ, b, a):
                continue
            succ[a].add(b)
            edges.append(Edge(a, b, "overlap", ", ".join(shared)))

    # Kahn's algorithm, always taking the best-ranked story that is free to go next.
    indegree = {k: 0 for k in by_key}
    for targets in succ.values():
        for t in targets:
            indegree[t] += 1
    ready = [(rank[k], k) for k, d in indegree.items() if d == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _, key = heapq.heappop(ready)
        order.append(key)
        for nxt in succ[key]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, (rank[nxt], nxt))

    return EpicOrder([k for k in order if not by_key[k].done], edges, overlaps, [], external, done)


# -- engine review -----------------------------------------------------------------------------
def _dicts(value: object) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _question(item: object) -> str:
    """A question as "KEY: text", whether the engine sent a string or {"story": ..., "question": ...}."""
    if isinstance(item, dict):
        text = str(item.get("question") or item.get("text") or "").strip()
        story = str(item.get("story") or "").strip()
        return f"{story}: {text}" if story and text else text
    return str(item).strip()


def review_epic(
    engine: CodeEngine, epic: Issue, stories: list[Issue], order: EpicOrder, max_lines: int, timeout_s: int
) -> EpicVerdict:
    """Ask the engine to read the whole Epic once: is it ready, and what must the stories agree on?"""
    by_key = {s.key: s for s in stories}
    ordered = [by_key[k] for k in order.order] + [by_key[k] for k in order.done]
    prompt = build_epic_plan_prompt(epic, ordered, order.overlaps, set(order.done), max_lines)
    # Empty scratch dir, like the readiness check: the review needs the ticket text only.
    with tempfile.TemporaryDirectory(prefix="dev-epic-", ignore_cleanup_errors=True) as scratch:
        result = engine.run(Path(scratch), prompt, timeout_s)
    if not result.ok:
        raise EpicPlanError(f"engine failed during the epic review: {result.error}")
    data = extract_json(result.response)
    if data is None or "ready" not in data:
        raise EpicPlanError(f"unparseable epic review reply: {result.response[:200]!r}")

    ready = bool(data["ready"])
    questions = [q for q in map(_question, data.get("questions") or []) if q]
    if not ready and not questions:
        questions = [DEFAULT_QUESTION]
    return EpicVerdict(
        ready=ready,
        questions=[] if ready else questions,
        contracts=_dicts(data.get("contracts")),
        conflicts=_dicts(data.get("conflicts")),
        oversized=_dicts(data.get("oversized")),
    )


# -- the whole plan ----------------------------------------------------------------------------
def plan_epic(jira: JiraClient, engine: CodeEngine | None, key: str, cfg: Config) -> EpicReport:
    """Read the Epic and its stories, order them, and (with an engine) review the Epic. Read-only."""
    epic = jira.get_issue(key)
    stories = jira.children(key)
    contracts = {s.key: parse_story(s) for s in stories}
    order = order_stories(stories, contracts)
    report = EpicReport(epic, stories, contracts, order)

    if epic.issue_type and epic.issue_type.lower() != "epic":
        report.warnings.append(f"{epic.key} is a {epic.issue_type}, not an Epic.")
    repo = cfg.repo_for(epic.project)
    if repo is None:
        report.problems.append(f"No repo is configured for Jira project {epic.project}.")
    if not stories:
        report.problems.append(f"{epic.key} has no child issues.")
    if order.cycle:
        report.problems.append("The stories block each other in a cycle: " + " -> ".join(order.cycle))
    for story_key, blockers in order.external_blockers.items():
        names = ", ".join(f"{b.key} ({b.status})" for b in blockers)
        report.warnings.append(f"{story_key} is blocked by {names}, outside this Epic.")
    for story in stories:
        if story.done:
            continue
        contract = contracts[story.key]
        if not contract.scenarios:
            report.warnings.append(f"{story.key} has no Gherkin acceptance scenarios.")
        if not contract.target_paths:
            report.warnings.append(f"{story.key} names no file paths under Target Files.")

    if engine is not None and not report.problems:
        max_lines = repo.spec_max_diff_lines if repo else 1500
        report.verdict = review_epic(engine, epic, stories, order, max_lines, cfg.engine_timeout_s)
    return report


def format_report(report: EpicReport, jira_base_url: str = "") -> str:
    epic, order = report.epic, report.order
    by_key = {s.key: s for s in report.stories}
    lines = [
        f"Epic {epic.key}: {epic.summary} ({epic.issue_type or 'issue'}, {epic.status or 'unknown status'})",
    ]
    if jira_base_url:
        lines.append(f"{jira_base_url.rstrip('/')}/browse/{epic.key}")
    lines.append(f"{len(report.stories)} stories: {len(order.done)} done, {len(order.order)} to implement.")

    if order.order:
        lines += ["", "Stack (implementation order; each story's PR is based on the one before it):"]
        for n, key in enumerate(order.order, 1):
            story, contract = by_key[key], report.contracts[key]
            side = {"backend": "BE", "frontend": "FE"}.get(contract.side, "--")
            lines.append(
                f"{n:>3}. {key} [{side}] {story.summary}  "
                f"({len(contract.scenarios)} AC, {len(contract.dod)} DoD, {story.status or '?'})"
            )
            after = [
                f"{e.before} (blocks)" if e.kind == "blocks" else f"{e.before} (shared files: {e.detail})"
                for e in order.predecessors(key)
                if e.before not in order.done
            ]
            if after:
                lines.append(f"       after {'; '.join(after)}")
    if order.done:
        lines.append(f"Already done: {', '.join(order.done)}")

    if order.overlaps:
        lines += ["", "Stories naming the same files (kept in order so each builds on the last):"]
        lines += [f"- {a} / {b}: {', '.join(paths)}" for a, b, paths in order.overlaps]

    for title, items in (("Problems (the Epic cannot start)", report.problems), ("Warnings", report.warnings)):
        if items:
            lines += ["", f"{title}:"] + [f"- {item}" for item in items]

    verdict = report.verdict
    if verdict is None:
        lines += ["", "Engine review: skipped."]
    else:
        lines += ["", f"Engine review: {'READY' if verdict.ready else 'NOT READY'}"]
        if verdict.questions:
            lines += ["Questions:"] + [f"- {q}" for q in verdict.questions]
        if verdict.contracts:
            lines.append("Shared contracts (every story must use these names and shapes):")
            for c in verdict.contracts:
                used = ", ".join(map(str, c.get("used_by") or [])) or "-"
                lines.append(f"- {c.get('name', '?')} (owner {c.get('owner', '?')}; used by {used}): {c.get('definition', '')}")
        if verdict.conflicts:
            lines.append("Conflicts:")
            for c in verdict.conflicts:
                stories = ", ".join(map(str, c.get("stories") or []))
                fix = f" Resolution: {c['resolution']}" if c.get("resolution") else ""
                lines.append(f"- [{stories}] {c.get('detail', '')}{fix}")
        if verdict.oversized:
            lines.append("Too large for one PR:")
            for o in verdict.oversized:
                split = f" Split: {o['split']}" if o.get("split") else ""
                lines.append(f"- {o.get('story', '?')}: {o.get('reason', '')}{split}")
    return "\n".join(lines)
