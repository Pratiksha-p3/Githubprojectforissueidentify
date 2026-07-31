# Chaos testing runbook

Stage 14's "chaos-test runbook" deliverable. This project has no live
multi-node staging environment to point a real chaos tool (Chaos Monkey,
Gremlin, `tc netem`, etc.) at in this development environment, so this
runbook does two things instead of one:

1. Documents each failure scenario and the *expected* system behavior,
   grounded in what's actually implemented (not aspirational) — the
   mechanism named in each scenario is real, with a file reference.
2. A subset of scenarios that don't require real infrastructure to
   exercise honestly are automated in `tests/test_chaos.py` (fakeredis
   for Redis failures, a broken connect function for Postgres, a
   failure-injecting fake for the LLM provider) — these run in CI on
   every push, so "does the system actually degrade gracefully" is a
   continuously-verified property, not just documentation that can drift
   out of date.

When real staging infrastructure exists, the scenarios below marked
**(manual)** are the ones to actually run a chaos tool against; the ones
marked **(automated)** already are, in `tests/test_chaos.py`.

---

## Scenario: Redis goes down mid-review

**Automated.** `IdempotencyStore` (`src/storage/idempotency_store.py`) and
`DLQStore` (`src/storage/dlq_store.py`) are both Redis-backed. If Redis is
unreachable when `try_mark_processed()` is called, the underlying
`redis.Redis` call raises — this propagates out of `_execute_review()`
uncaught (idempotency is not an auxiliary concern; a review that can't
prove it hasn't already run must not silently proceed as if it can).
Celery's `autoretry_for=(Exception,)` (`src/worker/tasks.py`) then retries
the task with backoff, so a transient Redis blip becomes a delayed retry,
not a dropped review or a duplicate one.

**Expected:** task fails and retries (up to `max_retries=3`); if Redis
is still down after all retries, the task lands in Celery's own failure
handling, and `_DLQOnFailureTask.on_failure()` attempts a DLQ push —
which itself depends on Redis, so this is the one true "everything is
down" case where the failure is only visible in Celery's own logs /
result backend, not durably recorded. This is a known, disclosed gap:
there is no non-Redis fallback for the DLQ in this stage's scope.

## Scenario: Postgres is unreachable during review persistence

**Automated.** `_execute_review()` wraps both `PostgresStore().save_review()`
and `AuditLog().record()` in their own `try/except`, printing and
continuing rather than failing the review
(`src/worker/tasks.py`, verified by `test_postgres_persistence_failure_does_not_fail_the_review`
and `test_audit_log_failure_does_not_fail_the_review` in `tests/test_tasks.py`)
— this was a deliberate Stage 10 design choice: history/audit persistence
is auxiliary record-keeping, not the core correctness path, and a review
that otherwise completed successfully must not be reported as failed just
because the database that stores its history is down.

**Expected:** the review completes and its `gate_decision` is reported
normally; the review is simply missing from history/audit trail until
Postgres recovers. `/health` (`src/core/health.py`) reports `"degraded"`
in this state via `check_postgres()`, so the gap is observable even
though it isn't blocking.

## Scenario: the LLM provider is down or timing out

**Automated (indirectly, via the circuit breaker unit tests) +
covered end-to-end here.** `src/core/circuit_breaker.py`'s breaker
trips after a burst of failures and short-circuits further calls
(`CircuitOpenError`) instead of letting every subsequent review queue up
its own three-attempt backoff against a provider that's already down.
`src/agents/_llm_finding_agent.py` catches `CircuitOpenError` the same as
any other LLM failure and returns `succeeded=False`.

**Expected:** `ReviewStatus.DEGRADED` (`src/core/orchestrator.py`) —
deterministic checkers still ran and their findings are still reported;
the PR gate (`src/core/pr_gate.py`) never auto-approves a `DEGRADED`
review. This is the central anti-silent-failure property this whole
rewrite was built around (see `src/core/models.py`'s `ReviewStatus`
docstring) — a downed LLM must never look identical to "reviewed
everything, found 0 issues."

## Scenario: two Celery workers process the same commit concurrently

**Automated**, but in `tests/test_load_concurrency.py` rather than
`tests/test_chaos.py` — this is a real concurrency-correctness test (real
Python threads against fakeredis), not a chaos/failure-injection test,
so it stays where it already was written (Stage 13) rather than being
duplicated here. See that file's docstring for the real race condition it
originally caught and the atomic `try_mark_processed()` fix.

## Scenario: a malformed/adversarial webhook payload

**(manual)** — send a webhook payload with missing fields, huge file
content, or an invalid HMAC signature against a running
`src/api/webhook.py` instance. `tests/test_webhook.py` covers the
signature-verification and malformed-event-type paths already; a full
chaos run here would additionally fuzz payload shapes against a live
process to check for unhandled exceptions rather than clean 4xx
responses — not automated in this stage's scope since it needs a running
HTTP server, not just function-level fakes.

## Scenario: DLQ grows unbounded (retry storm)

**(manual)** — if an upstream issue causes a large fraction of reviews to
fail all their retries, `DLQStore` (a Redis list with no cap) would grow
without bound. `metrics.increment("dlq_pushes_total")`
(`src/worker/tasks.py`, Stage 14) makes this observable via `/metrics`
once `settings.metrics_enabled=True`, but there is currently no
alerting threshold or automatic DLQ size cap wired to it — flagged here
as a known gap for whoever operates a real deployment of this to decide
a threshold for, rather than silently assumed to be fine.

---

## What this runbook deliberately does not cover

Real infrastructure chaos (killing a Kubernetes pod, partitioning a
network, disk-full on the Postgres host) needs real infrastructure to
mean anything — simulating those against fakes would just be re-testing
the same fakes, not learning anything about the real system. Once a real
staging environment exists, that's when the **(manual)** scenarios above
become genuine chaos-engineering exercises rather than documentation.
