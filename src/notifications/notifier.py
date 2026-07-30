"""
src/notifications/notifier.py

Sends alerts on ReviewResult events — Slack, Teams, and/or a JIRA
ticket, each independently configured and optional. Critically:
DEGRADED/FAILED status and any CRITICAL finding always trigger an alert
regardless of a severity threshold — "never silently degrade" is a real
guarantee only if someone actually gets paged about it, not just an
internal enum value nobody sees (src/core/models.py's ReviewStatus).

No channels configured means notify() safely no-ops (logged) — that's a
deliberate operator choice, not a failure. A configured channel that
fails to send IS logged as a failure per-channel, but doesn't stop the
other channels from being tried.
"""
from __future__ import annotations

import requests

from src.core.backoff import call_with_backoff
from src.core.config import settings
from src.core.models import ReviewResult, ReviewStatus, Severity

_MAX_FINDINGS_SHOWN = 5


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "rate limit", "timeout", "connection", "502", "503"))


def should_notify(result: ReviewResult) -> bool:
    """True if this result is significant enough to alert on. An
    incomplete review (DEGRADED/FAILED) always qualifies — that's
    exactly the "never silently degrade" property this project is built
    around — as does any critical finding on a completed review."""
    if result.status != ReviewStatus.COMPLETED:
        return True
    return result.critical_count > 0


class Notifier:
    def __init__(
        self,
        slack_webhook_url: str | None = None,
        teams_webhook_url: str | None = None,
        jira_base_url: str | None = None,
        jira_api_token: str | None = None,
        jira_project_key: str | None = None,
    ):
        self.slack_webhook_url = slack_webhook_url or settings.slack_webhook_url
        self.teams_webhook_url = teams_webhook_url or settings.teams_webhook_url
        self.jira_base_url = jira_base_url or settings.jira_base_url
        self.jira_api_token = jira_api_token or settings.jira_api_token
        self.jira_project_key = jira_project_key or settings.jira_project_key

    def notify(self, result: ReviewResult) -> dict:
        if not should_notify(result):
            return {"notified": False, "reason": "not significant enough to alert on"}

        sent = {}
        if self.slack_webhook_url:
            sent["slack"] = self._send_slack(result)
        if self.teams_webhook_url:
            sent["teams"] = self._send_teams(result)
        if self.jira_base_url and self.jira_api_token and self.jira_project_key:
            sent["jira"] = self._create_jira_ticket(result)

        if not sent:
            print("[notifier] No notification channels configured — alert not sent anywhere")
            return {"notified": False, "reason": "no channels configured"}

        return {"notified": True, "channels": sent}

    def _send_slack(self, result: ReviewResult) -> bool:
        return self._post_webhook(self.slack_webhook_url, _build_slack_payload(result), "slack")

    def _send_teams(self, result: ReviewResult) -> bool:
        return self._post_webhook(self.teams_webhook_url, _build_teams_payload(result), "teams")

    def _post_webhook(self, url: str, payload: dict, channel_name: str) -> bool:
        def _do_call() -> None:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()

        try:
            call_with_backoff(_do_call, should_retry=_is_retryable)
            return True
        except Exception as e:
            print(f"[notifier] {channel_name} alert failed: {e}")
            return False

    def _create_jira_ticket(self, result: ReviewResult) -> bool:
        url = f"{self.jira_base_url}/rest/api/2/issue"
        payload: dict = {
            "fields": {
                "project": {"key": self.jira_project_key},
                "summary": _jira_summary(result),
                "description": _jira_description(result),
                "issuetype": {"name": "Bug"},
            }
        }
        headers = {"Authorization": f"Bearer {self.jira_api_token}"}

        def _do_call() -> None:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()

        try:
            call_with_backoff(_do_call, should_retry=_is_retryable)
            return True
        except Exception as e:
            print(f"[notifier] JIRA ticket creation failed: {e}")
            return False


def _status_line(result: ReviewResult) -> str:
    if result.status != ReviewStatus.COMPLETED:
        return f"Review did not complete ({result.status.value})"
    return f"{result.critical_count} critical finding(s)"


def _build_slack_payload(result: ReviewResult) -> dict:
    critical_findings = [f for f in result.findings if f.severity == Severity.CRITICAL]
    findings_text = (
        "\n".join(
            f"• `{f.file}:{f.line}` — {f.message[:150]}"
            for f in critical_findings[:_MAX_FINDINGS_SHOWN]
        )
        or "No critical findings listed."
    )

    return {
        "text": f"AI Code Review Alert — {result.repo}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "\U0001f6a8 AI Code Review Alert"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Repository:*\n{result.repo}"},
                    {"type": "mrkdwn", "text": f"*Commit:*\n{result.commit_sha[:8]}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{_status_line(result)}"},
                ],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": findings_text}},
        ],
    }


def _build_teams_payload(result: ReviewResult) -> dict:
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": "AI Code Review Alert",
        "themeColor": "FF0000",
        "title": f"AI Code Review Alert — {result.repo}",
        "text": f"{_status_line(result)} on commit {result.commit_sha[:8]}.",
    }


def _jira_summary(result: ReviewResult) -> str:
    return f"[AI Code Review] {result.repo} — {_status_line(result)}"


def _jira_description(result: ReviewResult) -> str:
    lines = [
        f"Repo: {result.repo}",
        f"Commit: {result.commit_sha}",
        f"Status: {result.status.value}",
        "",
    ]
    for f in result.findings[:_MAX_FINDINGS_SHOWN]:
        lines.append(f"- {f.file}:{f.line} [{f.severity.value}] {f.message}")
    return "\n".join(lines)
