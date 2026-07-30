"""
src/storage/dlq_store.py

Dead-letter storage for reviews that failed even after Celery exhausted
its retries — so a failure is recorded for investigation instead of
silently vanishing, replacing the previous implementation's in-process
BackgroundTasks, which dropped work on crash with no record at all.

Redis-backed list for now (Stage 4's scope); Stage 8 introduces durable
Postgres-backed history for everything, DLQ included.
"""
from __future__ import annotations

import json
import time

import redis

from src.core.config import settings

_DLQ_KEY = "dlq:failed_reviews"


class DLQStore:
    def __init__(self, client: redis.Redis | None = None):
        self._client = client or redis.Redis.from_url(settings.redis_url)

    def push(self, *, repo: str, commit_sha: str, error: str) -> None:
        entry = json.dumps(
            {
                "repo": repo,
                "commit_sha": commit_sha,
                "error": error,
                "failed_at": time.time(),
            }
        )
        self._client.rpush(_DLQ_KEY, entry)

    def all(self) -> list[dict]:
        raw_entries = self._client.lrange(_DLQ_KEY, 0, -1)
        return [json.loads(e) for e in raw_entries]

    def count(self) -> int:
        return self._client.llen(_DLQ_KEY)
