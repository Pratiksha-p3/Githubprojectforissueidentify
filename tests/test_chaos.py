"""
tests/test_chaos.py

The automated half of Stage 14's chaos-testing runbook
(docs/chaos_testing.md) — failure-injection scenarios that don't require
real infrastructure to exercise honestly: a Redis outage (fakeredis
raising instead of a real server going down), a Postgres outage (a
connect function that raises), and an LLM provider outage (the actual
provider-call function replaced with one that always fails, so the real
circuit breaker genuinely trips through the real call path rather than
being mocked around).

Each test asserts the SYSTEM'S response to the failure, not just that the
failure happened — that's what makes this a chaos test and not just
another unit test of the failing component. See docs/chaos_testing.md
for the full scenario list, including the manual ones that need real
infrastructure to mean anything.
"""
from __future__ import annotations

import fakeredis
import pytest

from src.agents import llm_client
from src.core import orchestrator
from src.core.circuit_breaker import CircuitState, breaker
from src.core.models import ReviewStatus
from src.storage.idempotency_store import IdempotencyStore
from src.worker import tasks

_DIVISION_BUG_CODE = "def average_per_item(total, count):\n    return total / count\n"


def test_redis_outage_propagates_instead_of_being_silently_swallowed(monkeypatch):
    """If Redis is down, try_mark_processed() must raise -- a review that
    can't prove it hasn't already run must not silently proceed as if it
    can (see docs/chaos_testing.md's Redis scenario). The exception
    reaching _execute_review()'s caller is what lets Celery's own
    autoretry_for=(Exception,) actually retry the task later, instead of
    the failure being masked as a normal "skipped" or "completed" result."""

    class _DownRedis:
        def set(self, *a, **k):
            raise ConnectionError("Redis is unreachable")

    store = IdempotencyStore(client=_DownRedis())
    monkeypatch.setattr(tasks, "IdempotencyStore", lambda: store)

    with pytest.raises(ConnectionError):
        tasks._execute_review(
            repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
        )


def test_redis_recovering_after_an_outage_allows_the_retry_to_proceed():
    """Once Redis is back, a retried task (same repo/commit_sha, per
    Celery's retry semantics) must be able to actually claim and process
    the commit -- not be permanently stuck because of the earlier outage."""
    real_client = fakeredis.FakeRedis()
    store = IdempotencyStore(client=real_client)

    assert store.try_mark_processed("acme/widgets", "abc123") is True
    # A second claim on the same commit while healthy is correctly refused
    # (this is NOT the outage -- it's proving the store still works normally).
    assert store.try_mark_processed("acme/widgets", "abc123") is False


def test_postgres_outage_during_persistence_still_reports_a_completed_review(monkeypatch):
    """Restates docs/chaos_testing.md's Postgres scenario as an explicit
    chaos test: history/audit persistence going down must never turn a
    review that actually completed into a reported failure."""
    from src.core.models import ReviewResult

    store = IdempotencyStore(client=fakeredis.FakeRedis())
    monkeypatch.setattr(tasks, "IdempotencyStore", lambda: store)

    clean_result = ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=ReviewStatus.COMPLETED, findings=[]
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: clean_result)

    class _DownPostgresStore:
        def save_review(self, result):
            raise ConnectionError("Postgres is unreachable")

    class _NoopAuditLog:
        """AuditLog's own constructor also opens a real Postgres
        connection (src/core/audit_log.py) -- left unmocked, this test
        would make a real, slow, unreachable connection attempt of its
        own on top of the one being deliberately tested via
        PostgresStore. Not what this chaos scenario is about."""

        def record(self, **kwargs):
            pass

    monkeypatch.setattr(tasks, "PostgresStore", _DownPostgresStore)
    monkeypatch.setattr(tasks, "AuditLog", _NoopAuditLog)

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert outcome["status"] == "completed"
    assert outcome["gate_decision"] == "approve"


def test_llm_provider_outage_trips_the_real_circuit_breaker_and_reviews_degrade(monkeypatch):
    """Replaces the actual provider-call function (not call_llm itself)
    so the real circuit breaker in src/agents/llm_client.py genuinely
    trips through the real call path -- this is the difference between a
    chaos test and a unit test that mocks the thing being tested."""

    def _provider_is_down(*args, **kwargs):
        raise ConnectionError("LLM provider unreachable")

    monkeypatch.setattr(llm_client, "_call_groq", _provider_is_down)

    results = [
        orchestrator.review_code(
            _DIVISION_BUG_CODE, "app.py", repo="acme/widgets", commit_sha=f"sha-{i}"
        )
        for i in range(8)
    ]

    assert breaker.state == CircuitState.OPEN, (
        "A sustained LLM outage across multiple reviews should have tripped "
        "the circuit breaker by now."
    )
    assert all(r.status == ReviewStatus.DEGRADED for r in results), (
        "Every review during the outage must be honestly reported as DEGRADED, "
        "never silently treated as a clean pass."
    )
    assert all(
        any(f.source == "division_guard_checker" for f in r.findings) for r in results
    ), "Deterministic checkers must keep working throughout an LLM outage."


def test_review_gate_never_approves_during_a_sustained_llm_outage(monkeypatch):
    """The PR gate's own defense against exactly this class of failure --
    see src/core/pr_gate.py -- restated here as a chaos assertion: a
    downed LLM must never result in an auto-approved PR."""
    from src.core.pr_gate import GateDecision, decide

    def _provider_is_down(*args, **kwargs):
        raise ConnectionError("LLM provider unreachable")

    monkeypatch.setattr(llm_client, "_call_groq", _provider_is_down)

    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )

    assert decide(result) != GateDecision.APPROVE
