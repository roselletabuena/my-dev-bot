from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .engine import make_engine
from .github import GitHubClient, resolve_token
from .jira import JiraClient
from .lock import LockHeld, RunLock
from .notify import Notifier
from .pipeline import Pipeline
from .repo import RepoManager
from .state import State


def build_pipeline(config_path: Path, dry_run: bool) -> Pipeline:
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        raise SystemExit(f"config error in {config_path}: {exc}") from None
    token = resolve_token(cfg.secrets.github_token)
    if not token:
        raise SystemExit("No GitHub token: set GITHUB_TOKEN or run `gh auth login`.")
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    return Pipeline(
        cfg=cfg,
        jira=JiraClient(cfg.jira.base_url, cfg.secrets.jira_email, cfg.secrets.jira_api_token),
        repos=RepoManager(cfg.work_dir, token=token, user_name=cfg.git_user_name, user_email=cfg.git_user_email),
        github=GitHubClient(token),
        engine=make_engine(cfg.engine),
        state=State(cfg.work_dir / "state.sqlite"),
        notifier=Notifier(cfg.secrets.slack_webhook),
        dry_run=dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-bot", description="Jira tickets -> draft GitHub PRs")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--dry-run", action="store_true", help="do everything except Jira writes, push and PR")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="poll Jira once and process matching tickets")
    one = sub.add_parser("ticket", help="process a single ticket by key")
    one.add_argument("key")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
    pipeline = build_pipeline(args.config, args.dry_run)
    try:
        with RunLock(pipeline.cfg.work_dir / ".run.lock"):
            if args.command == "run":
                outcomes = pipeline.run_once()
            else:
                pipeline.repos.prune_stale()
                outcomes = [pipeline.process(pipeline.jira.get_issue(args.key))]
    except LockHeld as exc:
        print(f"skipped: {exc}", file=sys.stderr)
        return 0
    finally:
        pipeline.state.close()

    for o in outcomes:
        print(f"{o.key}: {o.status}" + (f" [{o.workflow}]" if o.workflow else "") + (f" {o.pr_url}" if o.pr_url else f" - {o.detail}" if o.detail else ""))
    return 1 if any(o.status == "failed" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
