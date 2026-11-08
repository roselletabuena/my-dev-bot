from __future__ import annotations

from dataclasses import dataclass, field

import requests


@dataclass
class Issue:
    key: str
    summary: str
    description: str
    project: str
    status: str = ""
    issue_type: str = ""  # Bug | Story | Task | ...
    labels: list[str] = field(default_factory=list)
    parent_key: str = ""  # parent epic, when the ticket has one
    parent_summary: str = ""


def adf_to_text(node: object) -> str:
    """Flatten an Atlassian Document Format tree to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content"))
    if kind in ("paragraph", "heading", "listItem", "codeBlock", "blockquote"):
        return inner.rstrip("\n") + "\n"
    return inner


def text_to_adf(text: str) -> dict:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.split("\n")
        if line.strip()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({"Accept": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}/rest/api/3{path}"

    def _issue(self, raw: dict) -> Issue:
        f = raw["fields"]
        return Issue(
            key=raw["key"],
            summary=f.get("summary", ""),
            description=adf_to_text(f.get("description")).strip(),
            project=f.get("project", {}).get("key", raw["key"].split("-")[0]),
            status=(f.get("status") or {}).get("name", ""),
            issue_type=(f.get("issuetype") or {}).get("name", ""),
            labels=list(f.get("labels") or []),
            parent_key=(f.get("parent") or {}).get("key", ""),
            parent_summary=((f.get("parent") or {}).get("fields") or {}).get("summary", ""),
        )

    def search(self, jql: str, max_results: int = 25) -> list[Issue]:
        resp = self.session.post(
            self._url("/search/jql"),
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": ["summary", "description", "project", "status", "issuetype", "labels", "parent"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return [self._issue(i) for i in resp.json().get("issues", [])]

    def get_issue(self, key: str) -> Issue:
        resp = self.session.get(self._url(f"/issue/{key}"), timeout=30)
        resp.raise_for_status()
        return self._issue(resp.json())

    def add_label(self, key: str, label: str) -> None:
        self._update_labels(key, {"add": label})

    def remove_label(self, key: str, label: str) -> None:
        self._update_labels(key, {"remove": label})

    def _update_labels(self, key: str, op: dict) -> None:
        resp = self.session.put(self._url(f"/issue/{key}"), json={"update": {"labels": [op]}}, timeout=30)
        resp.raise_for_status()

    def comment(self, key: str, text: str) -> None:
        resp = self.session.post(
            self._url(f"/issue/{key}/comment"), json={"body": text_to_adf(text)}, timeout=30
        )
        resp.raise_for_status()

    def transition(self, key: str, name: str) -> bool:
        """Move the issue via the transition called `name`. Returns False if unavailable."""
        resp = self.session.get(self._url(f"/issue/{key}/transitions"), timeout=30)
        resp.raise_for_status()
        wanted = name.lower()
        for t in resp.json().get("transitions", []):
            if t["name"].lower() == wanted or t.get("to", {}).get("name", "").lower() == wanted:
                post = self.session.post(
                    self._url(f"/issue/{key}/transitions"), json={"transition": {"id": t["id"]}}, timeout=30
                )
                post.raise_for_status()
                return True
        return False
