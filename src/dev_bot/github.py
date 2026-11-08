from __future__ import annotations

import subprocess
from dataclasses import dataclass

import requests


@dataclass
class PullRequest:
    number: int
    url: str


def resolve_token(configured: str = "") -> str:
    """GITHUB_TOKEN if set, else the local `gh` login."""
    if configured:
        return configured
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


class GitHubClient:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def find_open_pr(self, repo: str, branch: str) -> PullRequest | None:
        owner = repo.split("/")[0]
        resp = self.session.get(
            f"https://api.github.com/repos/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open"},
            timeout=30,
        )
        resp.raise_for_status()
        prs = resp.json()
        return PullRequest(prs[0]["number"], prs[0]["html_url"]) if prs else None

    def open_draft_pr(
        self, repo: str, branch: str, base: str, title: str, body: str, labels: list[str] | None = None
    ) -> PullRequest:
        existing = self.find_open_pr(repo, branch)  # retried ticket: force-pushed branch already has a PR
        if existing:
            return existing
        resp = self.session.post(
            f"https://api.github.com/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body, "draft": True},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        pr = PullRequest(data["number"], data["html_url"])
        if labels:  # labels are best-effort: a missing label must not fail the PR
            self.session.post(
                f"https://api.github.com/repos/{repo}/issues/{pr.number}/labels",
                json={"labels": labels},
                timeout=30,
            )
        return pr
