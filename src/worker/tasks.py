"""
src/worker/tasks.py

The Celery task wrapping the Stage 3 orchestrator, plus idempotency and
dead-letter handling — the durable async path that replaces the previous
implementation's in-process BackgroundTasks. Stage 5 closes the loop by
posting the result to GitHub (src/integrations/publisher.py) when a
pr_number is supplied and a token is configured; Stage 9 additionally
fires Slack/Teams/JIRA alerts (src/notifications/notifier.py) — the
notifier's own should_notify() gates on significance (DEGRADED/FAILED or
any critical finding), so this call is unconditional here rather than
duplicating that gating logic.

Stage 10 adds review-history persistence (src/storage/postgres_store.py)
and the audit trail (src/core/audit_log.py) — a real gap until now: the
Postgres store existed since Stage 8 but nothing actually called
save_review(), so the dashboard had no history to read. Both are
wrapped in their own try/except: Postgres being unreachable (e.g. no
docker-compose stack running locally) must never fail a review that
otherwise completed successfully — this is auxiliary record-keeping,
not the core correctness path.

Stage 13 fixed a genuine concurrency bug found by a real threading test
(tests/test_load_concurrency.py): the idempotency check used to be two
separate calls (already_processed() then mark_processed()), which let
every concurrent caller through when timed against each other — now a
single atomic claim (try_mark_processed()), released back
(idempotency.release()) if the work then fails, so Celery's retry can
still redo it instead of the failed attempt's claim silently blocking
the retry forever.

_execute_review() is deliberately plain business logic with no Celery
machinery in it (no `self`, no retry calls) so it's directly unit-
testable without needing a running broker or Celery's eager-execution
quirks. All Celery-specific behavior — retry count, exponential backoff,
what happens on final failure — is declared on the task decorator and
the _DLQOnFailureTask base class below, not hand-rolled inside the
function body.
"""
from __future__ import annotations

from typing import Any

from celery import Task

from src.core import metrics
from src.core.audit_log import AuditLog
from src.core.config import settings
from src.core.orchestrator import review_code
from src.core.pr_gate import decide, gate_reason
from src.integrations.publisher import publish_review
from src.notifications.notifier import Notifier
from src.storage.dlq_store import DLQStore
from src.storage.idempotency_store import IdempotencyStore
from src.storage.postgres_store import PostgresStore
from src.worker.celery_app import celery_app


def _execute_review(
    *,
    repo: str,
    commit_sha: str,
    filename: str,
    code: str,
    pr_number: int | None = None,
    include_llm: bool = True,
) -> dict:
    idempotency = IdempotencyStore()
    if not idempotency.try_mark_processed(repo, commit_sha):
        return {
            "status": "skipped",
            "reason": "already processed",
            "repo": repo,
            "commit_sha": commit_sha,
        }

    try:
        result = review_code(
            code, filename, repo=repo, commit_sha=commit_sha, include_llm=include_llm
        )
    except Exception:
        idempotency.release(repo, commit_sha)
        raise

    decision = decide(result)
    metrics.increment(f"reviews_{result.status.value}_total")

    outcome: dict[str, Any] = {
        "status": "completed",
        "repo": repo,
        "commit_sha": commit_sha,
        "review_status": result.status.value,
        "gate_decision": decision.value,
        "gate_reason": gate_reason(result),
        "findings_count": len(result.findings),
        "critical_count": result.critical_count,
    }

    if pr_number is not None and settings.github_token:
        outcome["publish"] = publish_review(result, pr_number)

    outcome["notification"] = Notifier().notify(result)

    try:
        PostgresStore().save_review(result)
    except Exception as e:
        print(f"[tasks] Failed to persist review history: {e}")

    try:
        AuditLog().record(
            actor="system",
            action="review",
            repo=repo,
            commit_sha=commit_sha,
            detail=f"status={result.status.value} gate={decision.value}",
        )
    except Exception as e:
        print(f"[tasks] Failed to write audit log: {e}")

    return outcome


class _DLQOnFailureTask(Task):
    """Fires exactly once, when Celery has exhausted every retry and the
    task fails for good — not on each intermediate retry attempt."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        DLQStore().push(
            repo=kwargs.get("repo", ""),
            commit_sha=kwargs.get("commit_sha", ""),
            error=str(exc),
        )
        metrics.increment("dlq_pushes_total")


@celery_app.task(
    bind=True,
    base=_DLQOnFailureTask,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def review_commit_task(
    self: Task,
    *,
    repo: str,
    commit_sha: str,
    filename: str,
    code: str,
    pr_number: int | None = None,
    include_llm: bool = True,
) -> dict:
    return _execute_review(
        repo=repo,
        commit_sha=commit_sha,
        filename=filename,
        code=code,
        pr_number=pr_number,
        include_llm=include_llm,
    )
