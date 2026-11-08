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
ROUTING_ORDER = (BUGFIX, AMEND, FEATURE, TASK)  # the order the classifier is offered them in


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
    """Cheap, deterministic signals first; the engine is asked only when they leave it open.

    Only the workflows `repo.workflows` allows can be chosen, so a repo without Spec Kit is never
    sent down the feature/amend path.
    """
    allowed = [wf for wf in ROUTING_ORDER if repo.allows(wf)]
    specs = list_specs(worktree, repo.spec_dir) if repo.spec_aware else []
    text = f"{issue.summary}\n{issue.description}"
    referenced = find_by_reference(specs, text, [issue.key])

    labelled = [wf for wf, label in cfg.jira.workflow_labels.items() if label in issue.labels]
    if labelled:
        wf, label = labelled[0], cfg.jira.workflow_labels[labelled[0]]
        if wf not in allowed:  # an explicit request for something this repo cannot do goes back to a human
            raise RoutingError(
                [f"The `{label}` label asks for the {wf} workflow, which {repo.name} does not run (it runs: {', '.join(allowed)}). Remove the label or pick another."]
            )
        return _finish(Route(wf, f"ticket carries the {label!r} label", referenced), specs)

    if issue.issue_type.lower() == "bug":
        if BUGFIX in allowed:
            return Route(BUGFIX, "Jira issue type is Bug", referenced)
        return Route(_fallback(allowed), "Jira issue type is Bug, but this repo does not run the bugfix workflow")

    if referenced and AMEND in allowed:
        return Route(AMEND, f"ticket points at existing spec {referenced.folder}", referenced)

    if len(allowed) == 1:  # nothing to decide, so do not spend an engine call on it
        return Route(allowed[0], f"the only workflow {repo.name} runs")

    return _classify(issue, specs, cfg, engine, allowed)


def _fallback(allowed: list[str]) -> str:
    """The workflow to use when no signal picks one: the small task if the repo runs it."""
    return TASK if TASK in allowed else allowed[0]


def _finish(route: Route, specs: list[Spec]) -> Route:
    if route.workflow == AMEND and route.spec is None:
        raise RoutingError(
            ["Which existing spec should this change? Quote its folder (for example `specs/015-core-voting-engine`) in the ticket."]
        )
    route.candidates = specs
    return route


def _classify(issue: Issue, specs: list[Spec], cfg: Config, engine: CodeEngine, allowed: list[str]) -> Route:
    # Empty scratch dir, like the readiness check: classification needs the ticket text and spec titles only.
    with tempfile.TemporaryDirectory(prefix="dev-route-") as scratch:
        result = engine.run(Path(scratch), build_routing_prompt(issue, specs, allowed), cfg.engine_timeout_s)
    data = extract_json(result.response) if result.ok else None
    workflow = str((data or {}).get("workflow", "")).lower()
    if workflow not in allowed:  # unusable, or a workflow this repo does not run
        reason = result.error if not result.ok else f"unusable routing reply {result.response[:120]!r}"
        fallback = _fallback(allowed)
        log.warning("%s: routing fell back to the %s workflow (%s)", issue.key, fallback, reason)
        return Route(fallback, f"classifier unavailable ({reason})")

    reason = str(data.get("reason") or "classified by the engine")
    if workflow != AMEND:
        return Route(workflow, reason)
    spec = next((s for s in specs if s.folder == data.get("spec")), None)
    if spec is None:  # amending a spec that does not exist would silently become a new feature
        raise RoutingError(["This looks like a change to an existing feature. Which spec folder under `specs/` does it modify?"])
    return Route(AMEND, reason, spec)
