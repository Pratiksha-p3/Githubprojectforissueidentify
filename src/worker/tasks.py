"""
src/worker/tasks.py

The Celery task wrapping the Stage 3 orchestrator, plus idempotency and
dead-letter handling — the durable async path that replaces the previous
implementation's in-process BackgroundTasks.

_execute_review() is deliberately plain business logic with no Celery
machinery in it (no `self`, no retry calls) so it's directly unit-
testable without needing a running broker or Celery's eager-execution
quirks. All Celery-specific behavior — retry count, exponential backoff,
what happens on final failure — is declared on the task decorator and
the _DLQOnFailureTask base class below, not hand-rolled inside the
function body.
"""
from __future__ import annotations

from celery import Task

from src.core.orchestrator import review_code
from src.core.pr_gate import decide, gate_reason
from src.storage.dlq_store import DLQStore
from src.storage.idempotency_store import IdempotencyStore
from src.worker.celery_app import celery_app


def _execute_review(
    *,
    repo: str,
    commit_sha: str,
    filename: str,
    code: str,
    include_llm: bool = True,
) -> dict:
    idempotency = IdempotencyStore()
    if idempotency.already_processed(repo, commit_sha):
        return {
            "status": "skipped",
            "reason": "already processed",
            "repo": repo,
            "commit_sha": commit_sha,
        }

    result = review_code(
        code, filename, repo=repo, commit_sha=commit_sha, include_llm=include_llm
    )
    idempotency.mark_processed(repo, commit_sha)

    decision = decide(result)
    return {
        "status": "completed",
        "repo": repo,
        "commit_sha": commit_sha,
        "review_status": result.status.value,
        "gate_decision": decision.value,
        "gate_reason": gate_reason(result),
        "findings_count": len(result.findings),
        "critical_count": result.critical_count,
    }


class _DLQOnFailureTask(Task):
    """Fires exactly once, when Celery has exhausted every retry and the
    task fails for good — not on each intermediate retry attempt."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        DLQStore().push(
            repo=kwargs.get("repo", ""),
            commit_sha=kwargs.get("commit_sha", ""),
            error=str(exc),
        )


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
    include_llm: bool = True,
) -> dict:
    return _execute_review(
        repo=repo,
        commit_sha=commit_sha,
        filename=filename,
        code=code,
        include_llm=include_llm,
    )
