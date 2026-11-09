"""Fill the target repo's PR template.

The engine writes the narrative sections (summary, key changes). Everything that is a verifiable
claim (which checks passed, their output, the Jira link) is written by the bot, never by the model.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from .engine.base import CodeEngine
from .guardrails import CheckResult
from .jira import Issue
from .prompt import build_pr_body, build_pr_description_prompt

log = logging.getLogger("dev_bot")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_OUTPUT_BLOCK = re.compile(r"```text\s*\n<!--[^\n]*(?:paste|output)[^\n]*-->\s*\n```", re.IGNORECASE)
_HEADING = re.compile(r"^##\s+.*$", re.MULTILINE)
_MAX_DIFF_CHARS = 40_000


def _strip_fence(text: str) -> str:
    """Models sometimes wrap the whole answer in a ```markdown fence."""
    m = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, re.DOTALL)
    return m.group(1) if m else text


def is_valid_fill(template: str, body: str) -> bool:
    """The filled body must keep every `##` heading of the template, in order."""
    wanted = [h.strip() for h in _HEADING.findall(template)]
    found = [h.strip() for h in _HEADING.findall(body)]
    return bool(wanted) and found[: len(wanted)] == wanted


def tick_passed_checks(body: str, results: list[CheckResult]) -> str:
    """Tick `- [ ] ... `cmd`` lines for commands the bot actually ran and saw pass."""
    passed = {r.command for r in results if r.ok}

    def tick(line: str) -> str:
        if re.match(r"\s*-\s\[\s\]", line) and any(f"`{cmd}`" in line for cmd in passed):
            return line.replace("[ ]", "[x]", 1)
        return line

    return "\n".join(tick(line) for line in body.split("\n"))


def terminal_summary(results: list[CheckResult], tail_lines: int = 4) -> str:
    parts = []
    for r in results:
        lines = [ln for ln in _ANSI.sub("", r.output_tail).splitlines() if ln.strip()][-tail_lines:]
        parts.append("\n".join([f"$ {r.command}  ->  {'PASS' if r.ok else 'FAIL'}", *lines]))
    return "\n\n".join(parts)


def finalize(body: str, issue: Issue, jira_url: str, results: list[CheckResult], repairs: int) -> str:
    body = tick_passed_checks(body, results)
    output = terminal_summary(results)
    if output:
        block = f"```text\n{output}\n```"
        if _OUTPUT_BLOCK.search(body):
            body = _OUTPUT_BLOCK.sub(lambda _m: block, body, count=1)
        else:
            body = f"{body.rstrip()}\n\n### Bot check output\n{block}"
    if issue.key not in body:  # the link must exist even if the model dropped it
        body = f"Jira: [{issue.key}]({jira_url}) - {issue.summary}\n\n{body}"
    note = f" Checks passed after {repairs} automatic fix round(s)." if repairs else ""
    return f"{body.rstrip()}\n\n_Opened as a draft by dev-bot.{note} Please review before merging._"


def build_description(
    engine: CodeEngine,
    issue: Issue,
    jira_base_url: str,
    template: str,
    diff: str,
    results: list[CheckResult],
    repairs: int,
    timeout_s: int,
) -> str:
    jira_url = f"{jira_base_url}/browse/{issue.key}"
    filled = _generate(engine, issue, jira_base_url, template, diff, timeout_s) if template.strip() else None
    if filled is None:
        return build_pr_body(jira_url, issue, results, repairs)
    return finalize(filled, issue, jira_url, results, repairs)


def _generate(
    engine: CodeEngine, issue: Issue, jira_base_url: str, template: str, diff: str, timeout_s: int
) -> str | None:
    clipped = diff if len(diff) <= _MAX_DIFF_CHARS else diff[:_MAX_DIFF_CHARS] + "\n... [diff truncated]"
    prompt = build_pr_description_prompt(issue, jira_base_url, template, clipped)
    try:
        # Empty scratch dir: the model only needs the text above, never repo or network access.
        with tempfile.TemporaryDirectory(prefix="dev-pr-", ignore_cleanup_errors=True) as scratch:
            result = engine.run(Path(scratch), prompt, timeout_s)
    except Exception:  # a description problem must never block an otherwise good PR
        log.exception("PR description generation crashed; using fallback")
        return None
    if not result.ok:
        log.warning("PR description generation failed (%s); using fallback", result.error)
        return None
    body = _strip_fence(result.response).strip()
    if not is_valid_fill(template, body):
        log.warning("PR description did not keep the template headings; using fallback")
        return None
    return body
