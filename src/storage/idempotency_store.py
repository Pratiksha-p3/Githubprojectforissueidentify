"""
src/storage/idempotency_store.py

Tracks which (repo, commit_sha) pairs are currently being or have been
processed, so a retried webhook delivery or two concurrent Celery
workers don't both do the real work for the same commit. Redis-backed
(Redis is already a project dependency from Stage 2's LLM cache).

try_mark_processed() is a single atomic Redis SET...NX call (check-and-
set in one round-trip), not two separate calls — confirmed via a real
concurrency test (tests/test_load_concurrency.py, using genuine Python
threads against fakeredis) that an earlier two-step "if not
already_processed(): mark_processed()" version let every one of 20
concurrent callers through, because the GIL can switch threads in the
gap between the check and the mark. That two-step API has been removed
entirely rather than left alongside the atomic one as a footgun.

release() exists so a caller that claims a commit via
try_mark_processed() but then fails to actually complete the work can
give the claim back — otherwise Celery's retry would see the commit as
"already processed" from the failed attempt and skip the retry
entirely, silently dropping work instead of redoing it.

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

    def try_mark_processed(
        self, repo: str, commit_sha: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> bool:
        """
        Atomically claims (repo, commit_sha). Returns True if this call
        is the one that claimed it (the caller should proceed with the
        real work), False if it was already claimed by someone else
        (the caller should skip).
        """
        return bool(self._client.set(_key(repo, commit_sha), "1", nx=True, ex=ttl_seconds))

    def release(self, repo: str, commit_sha: str) -> None:
        """Gives back a claim made via try_mark_processed() — call this
        when the claimed work fails, so a retry can actually redo it
        instead of being silently skipped as "already processed"."""
        self._client.delete(_key(repo, commit_sha))
