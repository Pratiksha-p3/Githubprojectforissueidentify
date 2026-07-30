"""
src/api/webhook.py

FastAPI webhook receiver for GitHub/GitLab push events. Verifies the
provider's signature before trusting the payload, enqueues a Celery task
(src/worker/tasks.py), and returns immediately — the HTTP response never
waits for a review to actually run, satisfying the "async, not blocking"
nonfunctional requirement by construction rather than by convention.

Real diff/file-content fetching from the GitHub/GitLab Contents API is a
later ingestion concern — the payload here still carries file content
directly (a `files: [{filename, content}, ...]` key), plus an optional
`pr_number` used by Stage 5's publisher (src/integrations/publisher.py)
to know which PR to comment on.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import FastAPI, Header, HTTPException, Request

from src.core.config import settings
from src.worker.tasks import review_commit_task

app = FastAPI(title="AI Code Review Webhook Receiver")


def verify_github_signature(payload: bytes, signature_header: str | None) -> None:
    if not settings.github_webhook_secret:
        return  # no secret configured — verification skipped (dev/local use)
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")

    expected = hmac.new(
        settings.github_webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid signature")


def verify_gitlab_token(token_header: str | None) -> None:
    if not settings.gitlab_webhook_secret:
        return
    if not token_header or not hmac.compare_digest(token_header, settings.gitlab_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    payload = await request.body()
    verify_github_signature(payload, x_hub_signature_256)

    if x_github_event != "push":
        return {"status": "ignored", "reason": f"event type '{x_github_event}' not handled"}

    data = await request.json()
    repo = data.get("repository", {}).get("full_name", "")
    commit_sha = data.get("after", "")
    pr_number = data.get("pr_number")
    files = data.get("files", [])

    task_ids = [
        review_commit_task.delay(
            repo=repo,
            commit_sha=commit_sha,
            filename=f["filename"],
            code=f["content"],
            pr_number=pr_number,
        ).id
        for f in files
    ]

    return {"status": "queued", "repo": repo, "commit_sha": commit_sha, "task_ids": task_ids}


@app.post("/webhook/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
) -> dict:
    verify_gitlab_token(x_gitlab_token)

    if x_gitlab_event != "Push Hook":
        return {"status": "ignored", "reason": f"event type '{x_gitlab_event}' not handled"}

    data = await request.json()
    repo = data.get("project", {}).get("path_with_namespace", "")
    commit_sha = data.get("after", "")
    pr_number = data.get("pr_number")
    files = data.get("files", [])

    task_ids = [
        review_commit_task.delay(
            repo=repo,
            commit_sha=commit_sha,
            filename=f["filename"],
            code=f["content"],
            pr_number=pr_number,
        ).id
        for f in files
    ]

    return {"status": "queued", "repo": repo, "commit_sha": commit_sha, "task_ids": task_ids}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
