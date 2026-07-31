"""
src/core/health.py

Stage 14 monitoring: real dependency health checks, not the trivial
`{"status": "ok"}` stub every FastAPI app (src/api/webhook.py,
src/dashboard/app.py, src/api/fix_actions.py) has returned unconditionally
since Stage 4/8/12 — a webhook receiver that's "up" but can't reach Redis
can still enqueue nothing and would previously have reported healthy the
whole time.

Each check is independent and swallows its own connection error rather
than letting it propagate — a health *check* that itself crashes the
health endpoint defeats the purpose. Short connect timeouts (2s) so a
completely unreachable host fails the check quickly instead of hanging
the request.

connect_fn/client are injectable so tests can exercise both the healthy
and unreachable paths without needing a real Redis/Postgres server (same
dependency-injection pattern as src/storage/idempotency_store.py and
src/storage/postgres_store.py).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
import redis

from src.core.circuit_breaker import breaker
from src.core.config import settings

_CONNECT_TIMEOUT_SECONDS = 2


def check_redis(client: redis.Redis | None = None) -> dict[str, Any]:
    try:
        c = client or redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        c.ping()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


def check_postgres(connect_fn: Callable[[], Any] | None = None) -> dict[str, Any]:
    connect_fn = connect_fn or (
        lambda: psycopg.connect(settings.database_url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)
    )
    try:
        conn = connect_fn()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


def check_llm_circuit_breaker() -> dict[str, Any]:
    """Not a pass/fail check on its own -- an OPEN breaker means the
    pipeline is correctly degrading (deterministic-only), not that
    anything is broken. Surfaced in the health report so an operator can
    see it without digging through logs, but doesn't affect overall
    status."""
    return {"state": breaker.state.value}


def full_health_report(
    *,
    redis_client: redis.Redis | None = None,
    postgres_connect_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    checks = {
        "redis": check_redis(redis_client),
        "postgres": check_postgres(postgres_connect_fn),
        "llm_circuit_breaker": check_llm_circuit_breaker(),
    }
    dependency_checks = (checks["redis"], checks["postgres"])
    overall = (
        "ok" if all(c["status"] == "ok" for c in dependency_checks) else "degraded"
    )
    return {"status": overall, "checks": checks}
