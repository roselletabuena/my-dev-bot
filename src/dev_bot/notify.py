from __future__ import annotations

import logging

import requests

log = logging.getLogger("dev_bot")


class Notifier:
    """Logs every message; also posts to a Slack incoming webhook when configured."""

    def __init__(self, slack_webhook: str = ""):
        self.slack_webhook = slack_webhook

    def send(self, text: str) -> None:
        log.info(text)
        if not self.slack_webhook:
            return
        try:
            requests.post(self.slack_webhook, json={"text": text}, timeout=10)
        except requests.RequestException as exc:  # never let notification failure break a run
            log.warning("Slack notification failed: %s", exc)
