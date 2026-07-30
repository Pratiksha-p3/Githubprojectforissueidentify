"""
src/storage/comment_store.py

Tracks which GitHub issue-comment ID was posted for a given (repo,
commit_sha), so re-running a review for the same commit updates the
existing comment instead of posting a duplicate — the idempotent
"upsert" behavior src/integrations/publisher.py relies on. Redis-backed,
same pattern as idempotency_store.py / dlq_store.py.
"""
from __future__ import annotations

import redis

from src.core.config import settings

_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _key(repo: str, commit_sha: str) -> str:
    return f"posted_comment:{repo}:{commit_sha}"


class CommentStore:
    def __init__(self, client: redis.Redis | None = None):
        self._client = client or redis.Redis.from_url(settings.redis_url)

    def get_comment_id(self, repo: str, commit_sha: str) -> int | None:
        value = self._client.get(_key(repo, commit_sha))
        return int(value) if value is not None else None

    def set_comment_id(self, repo: str, commit_sha: str, comment_id: int) -> None:
        self._client.set(_key(repo, commit_sha), str(comment_id), ex=_TTL_SECONDS)
