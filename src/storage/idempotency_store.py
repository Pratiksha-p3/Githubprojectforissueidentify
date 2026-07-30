"""
src/storage/idempotency_store.py

Tracks which (repo, commit_sha) pairs have already been reviewed, so a
retried webhook delivery or a retried Celery task doesn't produce a
duplicate review/comment for the same commit. Redis-backed (Redis is
already a project dependency from Stage 2's LLM cache) — a simple
existence check with a TTL, not a full database table, since "was this
already reviewed" only needs to answer yes/no for a bounded recent window.

The client is injectable so tests can pass a fakeredis instance instead
of needing a real Redis server.
"""
from __future__ import annotations

import redis

from src.core.config import settings

DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days — long enough to catch
# webhook/task retries and re-deliveries, short enough not to grow forever.


def _key(repo: str, commit_sha: str) -> str:
    return f"idempotency:{repo}:{commit_sha}"


class IdempotencyStore:
    def __init__(self, client: redis.Redis | None = None):
        self._client = client or redis.Redis.from_url(settings.redis_url)

    def already_processed(self, repo: str, commit_sha: str) -> bool:
        return bool(self._client.exists(_key(repo, commit_sha)))

    def mark_processed(
        self, repo: str, commit_sha: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._client.set(_key(repo, commit_sha), "1", ex=ttl_seconds)
