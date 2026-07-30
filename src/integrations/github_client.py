"""
src/integrations/github_client.py

GitHub REST API client — PAT-based for now (a fine-grained personal
access token scoped to one sandbox repo: contents:read,
pull_requests:write, checks:write — the least-privilege scopes this
client actually needs). A GitHub App (JWT -> installation token,
installable across multiple repos without a long-lived personal token)
is the "real" production auth path and a drop-in swap behind this same
interface later; nothing downstream should need to change when that
swap happens.

Retry/backoff reuses src/core/backoff.py rather than rolling its own —
a rate limit (429) or transient server error (502/503) is retried, an
auth failure (401/403) or not-found (404) is not, since retrying won't
fix either.

Check Run conclusion mapping is deliberately conservative: only
GateDecision.APPROVE ever maps to "success" — BLOCK maps to "failure"
and REVIEW_REQUIRED to "action_required", so an incomplete review
(DEGRADED/FAILED — see src/core/pr_gate.py) can never show up as a green
check on the PR itself, the same property the internal gate decision
already enforces.
"""
from __future__ import annotations

from typing import Any

import requests

from src.core.backoff import call_with_backoff
from src.core.config import settings
from src.core.pr_gate import GateDecision

_API_BASE = "https://api.github.com"

_CONCLUSION_BY_DECISION = {
    GateDecision.APPROVE: "success",
    GateDecision.BLOCK: "failure",
    GateDecision.REVIEW_REQUIRED: "action_required",
}


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "rate limit", "timeout", "connection", "502", "503"))


class GitHubClient:
    def __init__(self, token: str | None = None):
        self._token = token or settings.github_token
        if not self._token:
            raise RuntimeError("GITHUB_TOKEN is not set")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        def _do_call() -> dict:
            resp = requests.request(
                method, f"{_API_BASE}{path}", headers=self._headers(), timeout=15, **kwargs
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

        return call_with_backoff(_do_call, should_retry=_is_retryable)

    def post_issue_comment(self, repo: str, issue_number: int, body: str) -> dict:
        return self._request(
            "POST", f"/repos/{repo}/issues/{issue_number}/comments", json={"body": body}
        )

    def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict:
        return self._request(
            "PATCH", f"/repos/{repo}/issues/comments/{comment_id}", json={"body": body}
        )

    def create_check_run(
        self,
        repo: str,
        commit_sha: str,
        *,
        decision: GateDecision,
        summary: str,
        title: str = "AI Code Review",
    ) -> dict:
        conclusion = _CONCLUSION_BY_DECISION[decision]
        return self._request(
            "POST",
            f"/repos/{repo}/check-runs",
            json={
                "name": title,
                "head_sha": commit_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": title, "summary": summary},
            },
        )
