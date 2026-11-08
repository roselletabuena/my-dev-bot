"""Decides which workflow a ticket gets: bugfix, a new Spec Kit feature, an amendment to an existing one, or a small task."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, RepoConfig
from .engine.base import CodeEngine
from .jira import Issue
from .prompt import build_routing_prompt
from .specs import Spec, find_by_reference, list_specs
from .triage import extract_json

log = logging.getLogger("dev_bot")

BUGFIX, FEATURE, AMEND, TASK = "bugfix", "feature", "amend", "task"
WORKFLOWS = (BUGFIX, FEATURE, AMEND, TASK)


class RoutingError(RuntimeError):
    """The ticket cannot be routed without a human answer; `questions` go back on the ticket."""

    def __init__(self, questions: list[str]):
        super().__init__("; ".join(questions))
        self.questions = questions


@dataclass
class Route:
    workflow: str
    reason: str
    spec: Spec | None = None  # the existing spec an amend works on (and a bugfix may consult)
    candidates: list[Spec] = field(default_factory=list, repr=False)


def route_ticket(issue: Issue, worktree: Path, repo: RepoConfig, cfg: Config, engine: CodeEngine) -> Route:
    """Cheap, deterministic signals first; the engine is asked only when they leave it open."""
    specs = list_specs(worktree, repo.spec_dir)
    text = f"{issue.summary}\n{issue.description}"
    referenced = find_by_reference(specs, text, [issue.key])

    labelled = [wf for wf, label in cfg.jira.workflow_labels.items() if label in issue.labels]
    if labelled:
        return _finish(Route(labelled[0], f"ticket carries the {cfg.jira.workflow_labels[labelled[0]]!r} label", referenced), specs)

    if issue.issue_type.lower() == "bug":
        return Route(BUGFIX, "Jira issue type is Bug", referenced)

    if referenced:
        return Route(AMEND, f"ticket points at existing spec {referenced.folder}", referenced)

    return _classify(issue, specs, cfg, engine)


def _finish(route: Route, specs: list[Spec]) -> Route:
    if route.workflow == AMEND and route.spec is None:
        raise RoutingError(
            ["Which existing spec should this change? Quote its folder (for example `specs/015-core-voting-engine`) in the ticket."]
        )
    route.candidates = specs
    return route


def _classify(issue: Issue, specs: list[Spec], cfg: Config, engine: CodeEngine) -> Route:
    # Empty scratch dir, like the readiness check: classification needs the ticket text and spec titles only.
    with tempfile.TemporaryDirectory(prefix="dev-route-") as scratch:
        result = engine.run(Path(scratch), build_routing_prompt(issue, specs), cfg.engine_timeout_s)
    data = extract_json(result.response) if result.ok else None
    workflow = str((data or {}).get("workflow", "")).lower()
    if workflow not in WORKFLOWS:
        reason = result.error if not result.ok else f"unparseable routing reply {result.response[:120]!r}"
        log.warning("%s: routing fell back to the small-task workflow (%s)", issue.key, reason)
        return Route(TASK, f"classifier unavailable ({reason})")

    reason = str(data.get("reason") or "classified by the engine")
    if workflow != AMEND:
        return Route(workflow, reason)
    spec = next((s for s in specs if s.folder == data.get("spec")), None)
    if spec is None:  # amending a spec that does not exist would silently become a new feature
        raise RoutingError(["This looks like a change to an existing feature. Which spec folder under `specs/` does it modify?"])
    return Route(AMEND, reason, spec)
