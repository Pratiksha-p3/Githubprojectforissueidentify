"""
src/integrations/github_client.py

GitHub REST API client — two auth modes behind one interface, resolved
per-request so a token that expires mid-session (installation tokens
last ~1 hour) is transparently refreshed rather than cached stale for
the client's whole lifetime:

  1. Personal access token (settings.github_token, or an explicit
     `token` passed to the constructor — takes priority when given,
     e.g. in tests). Simple, but confirmed live against a real repo
     that the Check Runs API rejects it outright with a 403 regardless
     of granted scopes — GitHub only accepts App auth for that endpoint.
  2. GitHub App auth (src/integrations/github_app_auth.py) — used
     automatically when settings.github_app_id,
     settings.github_installation_id, and a readable
     settings.github_app_private_key_path are all configured and no
     explicit `token` was passed. This is what actually makes Check
     Runs work; PAT auth structurally cannot.

Falls back to the PAT if App auth isn't fully configured, so setting up
one doesn't require tearing down the other — a deployment can run on a
PAT with App credentials only partially filled in (e.g. the private key
file not deployed yet) without breaking.

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

get_pull_request()/list_pr_files()/get_file_content() close the gap
src/core/orchestrator.py's module docstring used to note explicitly:
real diff/file-content fetching from the GitHub API didn't exist yet —
the webhook receiver only ever accepted file content handed to it
directly in the payload. These three make it possible to point the
reviewer at a real, already-open PR (src/cli/review_pr.py) instead of
only ever a locally-supplied file.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import requests

from src.core.backoff import call_with_backoff
from src.core.config import settings
from src.core.pr_gate import GateDecision
from src.integrations import github_app_auth

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
        self._explicit_token = token
        has_pat = bool(self._explicit_token or settings.github_token)
        if not has_pat and not self._app_auth_configured():
            raise RuntimeError(
                "No GitHub auth configured -- set GITHUB_TOKEN (personal access "
                "token), or GITHUB_APP_ID + GITHUB_INSTALLATION_ID + a readable "
                "GITHUB_APP_PRIVATE_KEY_PATH (GitHub App auth)."
            )

    def _app_auth_configured(self) -> bool:
        return bool(
            settings.github_app_id
            and settings.github_installation_id
            and settings.github_app_private_key_path
            and Path(settings.github_app_private_key_path).is_file()
        )

    def _resolve_token(self) -> str:
        if self._explicit_token:
            return self._explicit_token
        if self._app_auth_configured():
            private_key_pem = Path(settings.github_app_private_key_path).read_text(
                encoding="utf-8"
            )
            return github_app_auth.get_installation_token(
                settings.github_app_id, settings.github_installation_id, private_key_pem
            )
        return settings.github_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._resolve_token()}",
            "Accept": "application/vnd.github+json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        # GitHub's response shape depends on the endpoint (a dict for
        # most, a bare list for e.g. list_pr_files) -- Any rather than
        # dict so list-returning callers don't need a type: ignore.
        def _do_call() -> Any:
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

    def get_pull_request(self, repo: str, pr_number: int) -> dict:
        """Returns the PR's own metadata — used for its head.sha (the
        commit to review and to attach the Check Run to)."""
        return self._request("GET", f"/repos/{repo}/pulls/{pr_number}")

    def list_pr_files(self, repo: str, pr_number: int) -> list[dict]:
        """Returns every file changed by the PR (filename + status —
        "added"/"modified"/"removed"/... — among other fields GitHub
        includes). Not paginated beyond GitHub's default page size (30);
        fine for this project's scope of reviewing individual PRs rather
        than bulk-scanning huge ones."""
        return self._request("GET", f"/repos/{repo}/pulls/{pr_number}/files")

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """Fetches a file's full text content at a specific ref (the
        PR's head sha, typically) via the Contents API — GitHub returns
        it base64-encoded regardless of file type, decoded here so
        callers get plain text directly."""
        return self._get_file_metadata(repo, path, ref)[0]

    def get_file_sha(self, repo: str, path: str, ref: str) -> str:
        """The file's current blob sha at `ref` — required by
        update_file_content() so GitHub can detect a stale write (the
        file changed since the caller last read it) instead of silently
        clobbering a concurrent commit."""
        return self._get_file_metadata(repo, path, ref)[1]

    def _get_file_metadata(self, repo: str, path: str, ref: str) -> tuple[str, str]:
        data = self._request("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref})
        return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

    def update_file_content(
        self, repo: str, path: str, *, message: str, content: str, sha: str | None = None,
        branch: str,
    ) -> dict:
        """Commits `content` via the Contents API (PUT) — creates `path`
        if it doesn't exist yet, or updates it if `sha` is given. `sha`
        must be the file's current blob sha (get_file_sha()) for an
        update; GitHub rejects the write with a 409 if it doesn't match
        the file's actual current content, rather than silently
        overwriting whatever's there. Omit `sha` entirely for a brand
        new file — GitHub treats a `sha` on a path with no existing
        file as an error, not "create it"."""
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body = {"message": message, "content": encoded, "branch": branch}
        if sha is not None:
            body["sha"] = sha
        return self._request("PUT", f"/repos/{repo}/contents/{path}", json=body)

    def create_review_comment(
        self,
        repo: str,
        pr_number: int,
        *,
        commit_id: str,
        path: str,
        line: int,
        body: str,
        start_line: int | None = None,
    ) -> dict:
        """Posts an inline comment anchored to a specific line (or line
        range) of the PR's diff — distinct from post_issue_comment(),
        which posts a top-level comment on the PR's conversation tab, not
        attached to any line. When `body` contains a fenced ```suggestion
        block, GitHub renders a one-click "Apply suggestion" button that
        commits the replacement text directly, which is what actually
        makes a Finding's `fix` actionable on the PR itself rather than
        just described in prose.

        `start_line` opts into GitHub's multi-line suggestion range (the
        API requires `start_line` to be a different, smaller value than
        `line`, with `start_side` set alongside it) -- needed for a fix
        like orchestrator.py's block-reindent, whose `fix` replaces more
        than one original line at once."""
        payload = {
            "body": body, "commit_id": commit_id, "path": path,
            "line": line, "side": "RIGHT",
        }
        if start_line is not None and start_line < line:
            payload["start_line"] = start_line
            payload["start_side"] = "RIGHT"
        return self._request(
            "POST",
            f"/repos/{repo}/pulls/{pr_number}/comments",
            json=payload,
        )
